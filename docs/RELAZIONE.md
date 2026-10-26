# Infrastructure as Code e confronto costi/prestazioni per una pipeline RAG sui documenti AIFA

**Simone Negro** — matricola 20051767 — Università del Piemonte Orientale
Progetto per il corso del prof. Massimo Canonico — bozza del 21/10/2026

> Stato della bozza: le sezioni 1–4, 6 (parte locale) e 7.1–7.2 sono scritte con dati misurati.
> Le parti segnate **⏳ Da completare** dipendono dagli esperimenti su Chameleon Cloud.

---

## Sommario

Il progetto parte da una pipeline RAG (*Retrieval-Augmented Generation*) costruita sui Foglietti
Illustrativi (FI) e sui Riassunti delle Caratteristiche del Prodotto (RCP) pubblicati da AIFA: circa
18.000 documenti PDF convertiti in Markdown, suddivisi in circa 805.000 frammenti e indicizzati in un
database vettoriale. Una prima versione ha eseguito l'elaborazione su una VM di Chameleon Cloud con GPU
H100, configurata a mano. Questa relazione descrive l'estensione concordata: rendere il provisioning
riproducibile come codice (*infrastructure as code*) e confrontare tempi e costi di configurazioni
diverse, locali e cloud, con e senza GPU.

⏳ *Da completare con i risultati principali (configurazione migliore per fase, punto di pareggio).*

---

## 1. Introduzione e obiettivi

Nella prima versione del progetto il cloud era usato come semplice potenza di calcolo a noleggio:
una VM prenotata dalla dashboard, configurata via SSH con uno script, rilasciata a mano. Su indicazione
del docente (scambio email del 28/09–14/10/2026), tra le tre estensioni proposte è stata scelta la terza:

> automatizzare il provisioning dell'infrastruttura (infrastructure as code) e confrontare costi e
> prestazioni di diverse configurazioni (solo CPU, GPU, locale e cloud).

Domande di ricerca:

1. **Quanto costa rendere l'infrastruttura riproducibile?** Tempo dal comando di provisioning alla VM
   pronta, rispetto alla durata del lavoro utile.
2. **Qual è la configurazione migliore per ciascuna fase?** Le fasi della pipeline hanno profili di
   carico diversi (rete, CPU, GPU).
3. **Una VM unica è uno spreco?** Confronto tra configurazione monolitica (una VM GPU per tutto) ed
   eterogenea (VM CPU per la conversione, VM GPU solo per l'embedding).
4. **Quando conviene il cloud rispetto al locale?** Punto di pareggio in funzione della dimensione del
   corpus.
5. **Come scala la conversione PDF → Markdown con il numero di core?**

---

## 2. Contesto: dati e pipeline

### 2.1 Dati

Tre file CSV dalla sezione *Liste dei farmaci* degli open data AIFA [1], scaricati il 09/07/2026:

| File | Righe | Contenuto |
|---|---|---|
| `atc.csv` | 7.209 | Classificazione ATC → descrizione |
| `PA_confezioni.csv` | 337.550 | Principi attivi per confezione (codice AIC) |
| `confezioni_fornitura.csv` | 159.708 | Anagrafica confezioni, con i link ai PDF di FI e RCP |

Le 159.708 confezioni corrispondono a 12.599 farmaci distinti (`COD_FARMACO`) e i PDF sono per farmaco:
deduplicando, i documenti da scaricare passano da circa 320.000 link a 25.198 (un FI e un RCP per
farmaco). Di questi, 17.962 sono disponibili; gli altri rispondono 404 (non pubblicati) o 500.

### 2.2 Pipeline

RAG ibrido [2]: le domande sui dati tabellari si risolvono in SQL (SQLite), il testo libero dei
documenti è indicizzato in un database vettoriale (ChromaDB [9]). Le fasi:

| Fase | Script | Cosa fa | Risorsa limitante |
|---|---|---|---|
| s1 | `s1_structured.py` | CSV → SQLite con indici | disco (secondi) |
| s2 | `s2_download.py` | download asincrono dei PDF, manifest in SQLite | rete, cortesia verso AIFA |
| s3 | `s3_pdf_to_md.py` | PDF → Markdown con PyMuPDF4LLM [7] e OCR Tesseract [8], in parallelo | CPU |
| s4 | `s4_index.py` | chunking per sezione, embedding con bge-m3 [3], indicizzazione | GPU |
| s5 | `s5_query.py` | retrieval (30 candidati) → reranking con bge-reranker-v2-m3 [4] (6 tenuti) → LLM | latenza |

Il dettaglio di ogni fase è in [LAVORO_SVOLTO.md](../LAVORO_SVOLTO.md).

---

## 3. Baseline v0: run manuale su Chameleon Cloud

Chameleon [5] è un testbed per la ricerca sui sistemi cloud basato su OpenStack; le risorse si
ottengono tramite *lease* (prenotazioni a tempo). La baseline è stata eseguita sul sito KVM@TACC:

- VM con 1 GPU NVIDIA H100 (94 GB), 48 vCPU, 250 GB di RAM, Ubuntu 22.04;
- lease e istanza creati dalla dashboard, configurazione con `chameleon/bootstrap.sh` via SSH;
- run completo con `chameleon/run_full.sh`, risultati riportati in locale con `rsync`.

Risultati (commit `93b93de`, tag `baseline-v0`):

| Fase | Cloud (H100 + 48 vCPU) | Note |
|---|---|---|
| s3, 17.959 documenti | 37 min | 48 processi paralleli, OCR attivo |
| s4, 804.995 chunk | ~2,5 h | batch 256; durata osservata, non misurata in modo controllato |
| s5, latenza per domanda | — | eseguita in locale: ~4,5 s con MPS, ~17,7 s su sola CPU |

Limiti di questa versione, che motivano l'estensione:
- provisioning manuale e non versionato;
- una sola configurazione misurata, quindi nessun confronto;
- nessuna stima dei costi;
- risorse inutilizzate: la H100 resta ferma durante s3, i 48 vCPU quasi inattivi durante s4;
- tempi in parte stimati o ricavati da un singolo batch.

---

## 4. Metodologia

### 4.1 Configurazioni

| ID | Dove | Risorse | Stato |
|---|---|---|---|
| `local-cpu` | Mac mini M4 (2024) | CPU 10 core (4 performance + 6 efficiency), 16 GB | ✅ misurata |
| `local-mps` | Mac mini M4 (2024) | GPU integrata 10 core via Metal (MPS) | ✅ misurata |
| `cloud-cpu-s` | Chameleon KVM@TACC | VM da 8 vCPU | ⏳ |
| `cloud-cpu-l` | Chameleon KVM@TACC | VM da 48 vCPU | ⏳ |
| `cloud-gpu` | Chameleon KVM@TACC | 1× H100 + 48 vCPU | ⏳ |
| `cloud-split` | Chameleon KVM@TACC | `cloud-cpu-l` per s3, `cloud-gpu` per s4 | ⏳ |

### 4.2 Campione fisso

Ripetere più volte il corpus completo su ogni configurazione non è sostenibile (circa 60 h di sola
indicizzazione sulla CPU locale, vedi §7.2), e ogni run ricontatterebbe AIFA per 25.000 download.
Si usa quindi un **campione fisso**, creato una volta con `bench/make_sample.sh 500`:

- i primi 500 farmaci in ordine di `COD_FARMACO` (ordinamento esplicito, quindi campione stabile);
- 1.000 PDF richiesti, **972 validi**, che producono **31.905 chunk**;
- archiviato come `samples/n500.tar.gz` (SQLite con il manifest + PDF), da caricare sull'object
  storage per le configurazioni cloud.

I risultati sul campione si estrapolano al corpus completo in modo lineare: s3 in base al numero di
documenti (× 17.959 / 972), s4 in base al numero di chunk (× 804.995 / 31.905). Il fattore di s4 è
diverso da quello di s3 perché i documenti del campione sono più corti della media (32,8 chunk per
documento contro 44,8 sul corpus completo; vedi §9).

### 4.3 Strumentazione

Ogni misura è un run di `bench/run.sh`, che:
1. crea una cartella `results/<config>/<run_id>/` con una copia del database del campione e un
   collegamento in sola lettura ai PDF, così ogni run parte dallo stesso stato e non tocca i dati
   della baseline;
2. esegue le fasi richieste registrando inizio, fine e durata in `phases.csv`, insieme a commit,
   host, numero di CPU, worker, device e dimensione del batch;
3. su Linux registra in background l'utilizzo di CPU (`vmstat`) e GPU (`nvidia-smi`).

`bench/collect.py` unisce tutti i run in `results/all.csv`, aggiungendo campione, documenti
convertiti e chunk indicizzati letti dalla cartella di ogni run.

### 4.4 Protocollo

- 3 ripetizioni per misura, si riporta la mediana e lo scarto (massimo − minimo);
- stesso commit per tutte le misure di una serie (misure locali: commit `1d7fca1`, con una sola
  modifica a un commento; risultati salvati nel commit `3093d06`);
- versioni bloccate in `rag/requirements.txt` (Python 3.13.4, PyTorch 2.13.0, sentence-transformers
  5.6.0, ChromaDB 1.5.9, PyMuPDF4LLM 1.28.0);
- macchina locale senza altri carichi durante le misure, con la sospensione di sistema disattivata
  (`caffeinate -i`);
- s4 su CPU e su MPS misurata con una sola ripetizione per la durata (2,4 h e 6,8 h); per run così
  lunghi la variabilità relativa attesa è bassa, ma il dato va letto come misura singola.

---

## 5. Infrastructure as code

⏳ *Da completare dopo l'implementazione.* Architettura prevista:

```
make up CONFIG=<id>
  ├─ lease Blazar [15] (python-chi [13])        prenotazione della risorsa
  ├─ Terraform + provider OpenStack [12]        keypair, security group, istanza, floating IP
  ├─ cloud-init [14]                            dipendenze, venv, clone del repository, campione
  ├─ bench/run.sh                               fasi misurate
  └─ make down                                  destroy e rilascio del lease
```

Misure previste: *time-to-ready* (dal comando alla VM pronta), durata del bootstrap, durata del
`destroy`, quota di overhead sul tempo totale pagato.

---

## 6. Modello di costo

Chameleon è gratuito per la ricerca e la didattica, quindi il costo in euro è **stimato** con listini
commerciali di risorse equivalenti. Tutti i prezzi sono IVA inclusa (22%), dal punto di vista di chi
paga davvero.

```
costo_cloud  = Σ_fasi (ore_fase + ore_overhead) × prezzo_orario_equivalente
costo_locale = ore × (ammortamento_orario + potenza_kW × prezzo_kWh)
```

Le ore fatturate comprendono provisioning, bootstrap e tempi morti, non solo il lavoro utile.

### 6.1 Costo orario della macchina locale

| Voce | Valore | Fonte |
|---|---|---|
| Prezzo Mac mini M4 (CPU 10 core, GPU 10 core, 16 GB, SSD 512 GB) | 839,00 € IVA inclusa | fattura Apple Store del 04/12/2024 ([copia con dati personali oscurati](fonti/fattura_mac_mini_redatta.pdf)) |
| Vita utile | 4 anni × 365 × 24 = 35.040 h | ipotesi |
| Potenza massima | 65 W (4 W a riposo) | Apple [10], configurazione M4 16 GB |
| Prezzo dell'energia | 0,4343 €/kWh, tasse incluse | ARERA [11], prezzo di riferimento IV trim. 2026 |

```
ammortamento = 839 / 35.040         = 0,0239 €/h
energia      = 0,065 kW × 0,4343    = 0,0282 €/h
totale                              = 0,0522 €/h
```

Ipotesi conservative da dichiarare:
- l'ammortamento su 24 h al giorno è il caso più favorevole al locale; con un uso effettivo minore il
  costo orario cresce;
- 65 W è il consumo massimo con CPU e GPU sature: è un limite superiore, il consumo reale durante s3 e
  s4 è probabilmente più basso. ⏳ *Eventuale misura diretta con `powermetrics`.*
- 0,4343 €/kWh è la tariffa di riferimento pubblica per i clienti vulnerabili, scelta perché
  verificabile; la tariffa di mercato libero può differire.

### 6.2 Listini cloud

⏳ *Da completare in `bench/prices.csv`:* almeno due fornitori per ciascuna classe di risorsa (1× H100,
48 vCPU, 8 vCPU), prezzo on-demand, data di rilevazione, fonte, cambio USD→EUR applicato.

---

## 7. Risultati

### 7.1 s3: scaling della conversione PDF → Markdown (locale)

Campione n500 (972 documenti), `local-cpu`, 3 ripetizioni per valore di worker.
Speedup `S(p) = T(1) / T(p)`, efficienza `E(p) = S(p) / p` [6].

| Worker *p* | Tempi (s) | Mediana (s) | Scarto | Doc/s | Speedup S(p) | Efficienza E(p) |
|---|---|---|---|---|---|---|
| 1 | 1115 · 1111 · 1134 | 1115 | 2,1% | 0,87 | 1,00 | 1,00 |
| 2 | 701 · 688 · 683 | 688 | 2,6% | 1,41 | 1,62 | 0,81 |
| 4 | 690 · 702 · 689 | 690 | 1,9% | 1,41 | 1,62 | 0,40 |
| 8 | 1000 · 968 · 984 | 984 | 3,3% | 0,99 | 1,13 | 0,14 |
| 10 | 1071 · 1077 · 1063 | 1071 | 1,3% | 0,91 | 1,04 | 0,10 |

⏳ *Grafico `docs/charts/s3_scaling.png` da `generate_charts.py`.*

Osservazioni:
- Lo scarto tra ripetizioni è sotto il 3,5% in tutti i casi: le differenze tra valori di *p* sono
  reali, non rumore.
- Lo speedup si ferma a 1,62 con 2 worker, resta uguale con 4 e **peggiora** oltre: con 10 worker il
  tempo torna quasi quello seriale.
- La legge di Amdahl [6] non spiega questo andamento. Dallo speedup misurato con 2 worker si ricava
  una frazione seriale f = 2/S(2) − 1 ≈ 0,23; con questa f Amdahl prevede S(4) ≈ 2,35 e S(10) ≈ 3,22.
  Soprattutto, Amdahl non prevede mai un rallentamento all'aumentare dei core: il calo osservato
  indica contesa su una risorsa condivisa.
- Ipotesi da verificare: (a) i processi si contendono i core con i thread interni delle librerie
  (*oversubscription*); (b) i 6 core efficiency del M4 sono più lenti e diventano il collo di
  bottiglia; (c) contesa sulla banda di memoria con 16 GB condivisi tra CPU e GPU.
  ⏳ *Test: `OMP_THREAD_LIMIT=1` con 8 worker; confronto con lo scaling sulla VM da 48 vCPU.*
- Le misure locali di s3 sono state eseguite **senza OCR** (Tesseract non installato sul Mac),
  mentre sulla VM cloud l'OCR è attivo: vedi §9.

Estrapolazione al corpus completo, configurazione migliore (2 worker):
688 s × 17.959 / 972 ≈ 12.700 s ≈ **3,5 h**, contro i 37 minuti della baseline cloud con OCR.

### 7.2 s4: embedding per device (locale)

Campione n500 (31.905 chunk), batch 64, 1 ripetizione. Il tempo comprende il caricamento del modello
e il chunking, che sul campione pesano poco rispetto all'embedding.

| Device | Tempo | Chunk/s | Estrapolazione 804.995 chunk |
|---|---|---|---|
| CPU (M4, 10 core) | 8.536 s (2 h 22 min) | 3,74 | ~215.000 s ≈ **60 h** |
| MPS (GPU M4) | 24.455 s (6 h 48 min) | 1,30 | ~617.000 s ≈ **171 h** |
| H100 (baseline, non controllata) | — | ~89 | ~2,5 h (osservate) |

Osservazioni:
- Sul Mac la GPU integrata è **2,9 volte più lenta** della CPU nell'embedding con bge-m3. Era già
  emerso nella baseline su un singolo batch (68 s su CPU contro 106 s su MPS); ora è confermato su
  31.905 chunk. Sul Mac la configurazione da usare per s4 è quindi la CPU.
- La stima di 60 h sul corpus completo è coerente con le ~50 h stimate nella prima versione.
- ⏳ *H100 da rimisurare sul campione con `bench/run.sh` (stesso protocollo).*

### 7.3 Configurazioni cloud

⏳ *Da completare: s3 su `cloud-cpu-s` e `cloud-cpu-l` (scaling da 1 a 48 worker), s3 e s4 su
`cloud-gpu`, utilizzo di CPU e GPU per fase, time-to-ready.*

### 7.4 Tempo e costo

Stima preliminare per il corpus completo, calcolata a mano con i dati di §7.1–7.2 e il costo orario di
§6.1 (⏳ *da sostituire con l'output di `bench/cost_model.py`*):

| Configurazione | s3 | s4 | Tempo totale | Costo |
|---|---|---|---|---|
| `local-cpu` (s3 con 2 worker, s4 su CPU) | 3,5 h | 60 h | ~63,5 h | ~3,3 € |
| `local-mps` (s3 con 2 worker, s4 su MPS) | 3,5 h | 171 h | ~175 h | ~9,1 € |

In locale il costo in euro è basso; il prezzo vero è il tempo: quasi tre giorni di calcolo continuo.

⏳ *Da completare: confronto con le configurazioni cloud, grafico tempo/costo con frontiera di Pareto,
punto di pareggio locale/cloud in funzione della dimensione del corpus.*

---

## 8. Discussione delle ipotesi

| Ipotesi | Stato |
|---|---|
| **H1** — La configurazione eterogenea costa meno della monolitica a tempo quasi uguale | ⏳ |
| **H2** — s3 scala quasi linearmente fino a un certo numero di core, poi l'efficienza cala | in locale **non confermata nella forma prevista**: l'efficienza cala già da 2 worker e oltre 4 il tempo peggiora (§7.1); ⏳ verifica sulla VM da 48 vCPU |
| **H3** — Sotto una certa dimensione del corpus il locale vince, per l'overhead fisso del cloud | ⏳ richiede il time-to-ready |
| **H4** — Per l'interrogazione (s5) il cloud non conviene | ⏳ |

---

## 9. Limiti e minacce alla validità

- **OCR assente nelle misure locali di s3.** Tesseract non è installato sul Mac, quindi PyMuPDF4LLM
  non ha eseguito l'OCR sulle pagine-immagine; sulla VM cloud l'OCR è attivo, e nella baseline portava
  il tempo per documento da 0,07 s a 6,9 s. Il confronto locale/cloud di s3 è valido solo a parità di
  software. ⏳ *Ripetere le misure locali di s3 con Tesseract installato, oppure eseguire anche sul
  cloud una serie senza OCR.*
- **Rappresentatività del campione.** I primi 500 farmaci per codice sono i più vecchi e hanno
  documenti più corti della media (32,8 contro 44,8 chunk per documento). L'estrapolazione per chunk
  corregge s4; per s3 la durata per documento potrebbe essere sottostimata.
- **Estrapolazione lineare.** Ignora i costi fissi (caricamento del modello, avvio dei processi), che
  sul corpus completo pesano ancora meno: per questo aspetto la stima è prudente.
- **Ripetizioni di s4 in locale.** Una sola, per la durata dei run.
- **Potenza del Mac.** Valore massimo dichiarato, non misurato.
- **Prezzi.** Listini commerciali usati come equivalenti: Chameleon è gratuito e le risorse non sono
  identiche (virtualizzazione, rete, disco). I prezzi cambiano nel tempo, per questo ognuno ha data e
  fonte.

---

## 10. Conclusioni

⏳ *Da completare: quale configurazione per quale fase, quanto costa la riproducibilità, quando conviene
il cloud.*

---

## Appendice A — Riproduzione delle misure locali

```bash
python -m venv .venv && .venv/bin/pip install -r rag/requirements.txt
bench/make_sample.sh 500                                   # campione fisso
for w in 1 2 4 8 10; do for r in 1 2 3; do
  CONFIG=local-cpu WORKERS=$w bench/run.sh samples/n500 s3 # scaling s3
done; done
CONFIG=local-cpu DEVICE=cpu bench/run.sh samples/n500      # s3 + s4 su CPU
CONFIG=local-mps DEVICE=mps bench/run.sh samples/n500      # s3 + s4 su MPS
.venv/bin/python bench/collect.py                          # -> results/all.csv
```

---

## Riferimenti

1. AIFA — Agenzia Italiana del Farmaco, *Liste dei farmaci* (open data).
   https://www.aifa.gov.it/it/liste-dei-farmaci — consultato il 09/07/2026.
2. P. Lewis et al., *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*, NeurIPS 2020.
   arXiv:2005.11401.
3. J. Chen, S. Xiao, P. Zhang, K. Luo, D. Lian, Z. Liu, *BGE M3-Embedding: Multi-Lingual,
   Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation*, 2024.
   arXiv:2402.03216.
4. BAAI, *bge-reranker-v2-m3*. https://huggingface.co/BAAI/bge-reranker-v2-m3
5. K. Keahey et al., *Lessons Learned from the Chameleon Testbed*, USENIX Annual Technical Conference
   (ATC '20), 2020.
6. G. M. Amdahl, *Validity of the single processor approach to achieving large scale computing
   capabilities*, AFIPS Spring Joint Computer Conference, 1967.
7. Artifex, *PyMuPDF4LLM*. https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/
8. *Tesseract OCR*. https://github.com/tesseract-ocr/tesseract
9. *Chroma*. https://docs.trychroma.com
10. Apple, *Mac mini power consumption and thermal output (BTU) information*.
    https://support.apple.com/103253 — consultato il 21/10/2026.
11. ARERA, comunicato stampa del 29/09/2026, *Elettricità: maggior tutela +37,3% nel IV trimestre 2026
    per i clienti vulnerabili*.
    https://www.arera.it/comunicati-stampa/dettaglio/elettricita-maggior-tutela-373-nel-iv-trimestre-2026-per-i-clienti-vulnerabili
12. *Terraform OpenStack Provider*.
    https://registry.terraform.io/providers/terraform-provider-openstack/openstack/latest/docs
13. Chameleon Cloud, *python-chi*. https://github.com/ChameleonCloud/python-chi
14. *cloud-init documentation*. https://cloudinit.readthedocs.io
15. OpenStack, *Blazar — Resource Reservation Service*. https://docs.openstack.org/blazar/latest/
