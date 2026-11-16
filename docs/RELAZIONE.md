# Infrastructure as Code e confronto costi/prestazioni per una pipeline RAG sui documenti AIFA

**Simone Negro** — matricola 20051767 — Università del Piemonte Orientale
Progetto per il corso del prof. Massimo Canonico — 01/11/2026

---

## Sommario

Il progetto parte da una pipeline RAG (*Retrieval-Augmented Generation*) costruita sui Foglietti Illustrativi (FI) e sui Riassunti delle Caratteristiche del Prodotto (RCP) pubblicati da AIFA. La pipeline converte circa 18.000 documenti da PDF a Markdown, li divide in circa 805.000 frammenti e li indicizza in un database vettoriale. Una prima versione ha eseguito l'elaborazione su una VM di Chameleon Cloud con GPU H100, configurata a mano. Questa relazione descrive l'estensione concordata di rendere il provisioning riproducibile come codice (*infrastructure as code*) e, in seguito, di confrontare tempi e costi di configurazioni diverse, locali e cloud, con e senza GPU.

Le misure le ho eseguite in due tornate. Dopo la prima ho allineato le versioni del software su tutte le macchine e ho ripetuto tutto, eseguendo davvero anche la configurazione eterogenea. I numeri qui sotto sono quelli della seconda tornata.

I risultati principali sono:
- con Terraform e un Makefile la VM è raggiungibile in **~70 secondi**, contro i ~7 minuti della procedura manuale dalla dashboard di Chameleon Cloud. La VM è pronta per le misure in **~3,5 minuti** (5 minuti nella prima tornata, prima di installare l'ambiente con uv), mentre la distruzione (~20 s) rilascia tutte le risorse, compreso l'IP pubblico che a mano era stato dimenticato (§5);
- la conversione dei file da PDF a Markdown (fase s3) scala quasi linearmente sulle VM cloud (efficienza di almeno 0,92 con 8 worker). Sul Mac all'inizio si fermava a 2 worker, per i thread interni di una libreria, e dopo la correzione converte il campione con 10 worker quanto una VM da 16 vCPU (§7.1–7.2);
- l'embedding (fase s4) sulla H100 è ~29 volte più veloce della CPU del Mac e 53–86 volte più veloce delle VM da 16 e 8 vCPU: per il corpus completo servono 2 h 43 min contro giorni (§7.3);
- nella configurazione monolitica la GPU resta inutilizzata per il **54%** del tempo pagato (download e conversione), argomento che va a favore della configurazione eterogenea (§7.4);
- il campione fisso **sottostima** il corpus completo dal 25% al 56% per s3, a seconda della macchina, e del 36–37% per s4, perché i suoi documenti hanno frammenti più corti (§7.5);
- la configurazione eterogenea eseguita sul corpus completo (s3 su una VM solo CPU, s4 sulla GPU) costa l'8–10% in meno della monolitica, ma impiega il 63% di tempo in più. Spostando anche il download sulla VM CPU il risparmio sale al 44–46% (§7.6);
- il fornitore pesa più dell'architettura: s3 + s4 sul corpus completo costano da 11,69 € (H100 su RunPod) a 32,89 € (H100 su Azure) (§7.6);
- l'overhead fisso di ~3,5 minuti aumenta del 57% il costo di un run sul campione con la H100 e dell'1,8% quello sul corpus completo. In tempo, il cloud batte il Mac già sopra ~25 documenti, mentre riguardo al costo in euro, il Mac resta più economico a ogni dimensione, con lo svantaggio che il corpus completo gli richiederebbe ~3,4 giorni (§7.7);
- per rispondere a una domanda (fase s5) la H100 è 16 volte più veloce del Mac, ma ogni domanda costa da 4 a 12 volte di più, quindi per un uso personale conviene il locale (§7.8);
- con EmbeddingGemma 2 al posto di bge-m3 l'indicizzazione del corpus completo è 2,1 volte più veloce, e la ricerca trova più spesso il farmaco giusto (§7.9).

---

## 1. Introduzione e obiettivi

Nella prima versione del progetto il cloud era usato come semplice potenza di calcolo a noleggio: la VM veniva prenotata dalla dashboard, configurata via SSH con uno script e rilasciata a mano. Su indicazione del docente (scambio email del 28/09–14/10/2026), tra le tre estensioni proposte è stata scelta la terza:

> automatizzare il provisioning dell'infrastruttura (infrastructure as code) e confrontare costi e prestazioni di diverse configurazioni (solo CPU, GPU, locale e cloud).

Domande di ricerca:

1. **Quanto costa rendere l'infrastruttura riproducibile?** Si misura il tempo dal comando di provisioning alla VM pronta e lo si confronta con la durata del lavoro utile.
2. **Qual è la configurazione migliore per ciascuna fase?** Le fasi della pipeline hanno profili di carico diversi (rete, CPU, GPU).
3. **Una VM unica è uno spreco?** Si confrontano la configurazione monolitica (una VM GPU per tutto) e quella eterogenea (una VM CPU per la conversione, una VM GPU solo per l'embedding).
4. **Quando conviene il cloud rispetto alla macchina locale?** Si calcola il punto di pareggio in funzione della dimensione del corpus.
5. **Come scala la conversione da PDF a Markdown con il numero di core?** Si misura il tempo con 1, 2, 4... worker, fino a tutti i core disponibili.

---

## 2. Contesto: dati e pipeline

### 2.1 Dati

Il 09/07/2026 ho scaricato tre file CSV dalla sezione *Liste dei farmaci* (paragrafo *Anagrafica dei farmaci*) degli open data AIFA [1]:

| File | Righe | Contenuto |
|---|---|---|
| `atc.csv` | 7.209 | Classificazione ATC e relativa descrizione |
| `PA_confezioni.csv` | 337.550 | Principi attivi per confezione (codice AIC) |
| `confezioni_fornitura.csv` | 159.708 | Anagrafica confezioni, con i link ai PDF di FI e RCP |

Le 159.708 confezioni corrispondono a 12.599 farmaci distinti (`COD_FARMACO`). I PDF però sono per farmaco, non per confezione. Raggruppando per farmaco, i link da scaricare passano da circa 320.000 a 25.198 (un FI e un RCP per farmaco). Di questi, circa 18.000 sono disponibili (17.959 nella baseline, 17.984 nel full run del 28/10/2026); gli altri rispondono con errore 404 (non pubblicati) o 500 (errore del server).

### 2.2 Pipeline

La pipeline è un RAG [2]: per rispondere a una domanda cerca nei documenti i passaggi più pertinenti e li passa a un modello linguistico, che risponde basandosi su quelli. È *ibrido* perché le domande sui dati tabellari (per esempio "quali farmaci contengono il principio attivo X") si risolvono in SQL su SQLite, mentre il testo dei documenti è indicizzato in un database vettoriale (ChromaDB [9]). 

La pipeline è composta da cinque fasi distinte:

| Fase | Script | Cosa fa | Risorsa limitante |
|---|---|---|---|
| s1 | `s1_structured.py` | conversione da CSV a SQLite con indici | disco (pochi secondi) |
| s2 | `s2_download.py` | download asincrono dei PDF, elenco dei file in SQLite | rete, limite di richieste per non sovraccaricare AIFA |
| s3 | `s3_pdf_to_md.py` | conversione da PDF a Markdown con PyMuPDF4LLM [7] e OCR Tesseract [8], in parallelo | CPU |
| s4 | `s4_index.py` | divisione in frammenti (chunking) per sezione, embedding con bge-m3 [3], indicizzazione | GPU |
| s5 | `s5_query.py` | ricerca di 30 frammenti candidati → riordino con bge-reranker-v2-m3 [4], ne restano 6 → risposta del modello linguistico | latenza |

Di seguito elenco le varie fasi spiegate nel dettaglio, mentre per il diario completo della prima versione si può leggere [LAVORO_SVOLTO.md](../LAVORO_SVOLTO.md).

#### 2.2.1 s1, dati strutturati

Nella prima fase carico i tre CSV in dati strutturati all'interno di SQLite, andando a leggere tutte le colonne come testo. Questo perché i codici AIC hanno degli zeri iniziali (`000152037`) e, se andassi a leggerli come numeri, gli zeri sparirebbero e farebbero fallire i collegamenti tra le tabelle.

#### 2.2.2 s2, download

In seguito scarico i PDF in parallelo, ma con al massimo 6 richieste alla volta per non sovraccaricare i server AIFA (altrimenti il server potrebbe rispondere con un timeout per le troppe richieste effettuate in poco tempo), e ritento fino a 3 volte in caso di errore. L'esito di ogni download (riuscito, 404, 500) finisce in una tabella SQLite, in modo da tenere traccia di quali farmaci abbiano dato errore e da far ripartire un run interrotto da dove si era fermato, sapendo quali documenti mancano e perché.

#### 2.2.3 s3, conversione

Nella fase di conversione, PyMuPDF4LLM trasforma ogni PDF in Markdown mantenendo titoli, elenchi e tabelle, e passa all'OCR le pagine che in realtà sono immagini (per esempio i documenti scansionati, in modo da non perderne il contenuto). Siccome i documenti sono indipendenti tra loro, li converto in parallelo su più processi (*worker*), in modo da velocizzare il processo di conversione. Alla fine della conversione, cancello il PDF e tengo solo il Markdown (~1 GB in tutto). Ho deciso di cancellare i PDF perché avrebbero occupato spazio inutile, siccome ho lo stesso documento convertito in MD. Ho deciso di convertire tutti i PDF perché i file MD sono di più facile lettura, interpretazione e possibile modifica. Inoltre gli LLM fanno meno fatica a estrarre informazioni da un testo in Markdown, e i titoli che il Markdown conserva servono in s4 per dividere i documenti per sezione. Questa scelta è stata fatta anche in vista di un possibile ampliamento del progetto.

#### 2.2.4 s4, indicizzazione

Durante l'indicizzazione ho deciso di togliere il testo legale che AIFA ripete in ogni documento, tenendo solo i dati strettamente necessari. Essendo uguale in tutti i documenti, quel testo li farebbe sembrare simili tra loro e peggiorerebbe la ricerca. La pulizia avviene in memoria, quindi i file MD restano invariati. Poi divido ciascun documento in frammenti (*chunk*) seguendo le sezioni numerate standard di FI e RCP (per esempio "4.3 Controindicazioni"). Le sezioni più lunghe di 1.800 caratteri vengono spezzate con 150 caratteri di sovrapposizione. Prima di calcolare l'embedding aggiungo a ogni frammento il nome del farmaco, il tipo di documento e la sezione, così il vettore "sa" a cosa si riferisce. In ChromaDB ogni frammento è salvato con il suo vettore e con i metadati (codice del farmaco, ATC, ditta), che collegano i due livelli e permettono di filtrare la ricerca.

#### 2.2.5 s5, interrogazione

Infine c'è la fase di interrogazione, in cui la domanda diventa un vettore con lo stesso modello usato in s4 e vengono recuperati i 30 frammenti più vicini. Un secondo modello (*reranker*), più lento ma più preciso, rilegge ogni coppia domanda–frammento e tiene i 6 migliori. Questi passano al modello linguistico (Claude, oppure un modello locale con Ollama), a cui chiedo nel prompt di rispondere solo in base ai frammenti, di dire se l'informazione manca e di citare sempre il farmaco da cui la prende.

---

## 3. Baseline v0: run manuale su Chameleon Cloud

Chameleon [5] è un testbed per la ricerca sui sistemi cloud basato su OpenStack. Le risorse si
ottengono tramite *lease* (prenotazioni a tempo), che hanno una durata scelta dall'utente. Ho eseguito la baseline sul sito KVM@TACC, creando le risorse a mano dalla dashboard web.

Nel dettaglio:
- VM con 1 GPU NVIDIA H100 (94 GB), 48 vCPU, 250 GB di RAM, Ubuntu 22.04;
- lease e istanza creati dalla dashboard, configurazione con `chameleon/bootstrap.sh` via SSH;
- run completo con `chameleon/run_full.sh`, risultati riportati in locale con `rsync`.

Risultati (commit `93b93de`, tag `baseline-v0`):

| Fase | Cloud (H100 + 48 vCPU) | Note |
|---|---|---|
| s3, 17.959 documenti | 37 min | 48 processi paralleli, OCR attivo |
| s4, 804.995 chunk | ~2,5 h | batch 256; durata osservata, non misurata in modo controllato |
| s5, latenza per domanda | — | eseguita in locale: ~4,5 s con MPS, ~17,7 s su sola CPU (misurata poi anche sul cloud, §7.8) |

Limiti di questa versione, che motivano l'estensione:
- provisioning manuale e non versionato;
- una sola configurazione misurata, quindi nessun confronto;
- nessuna stima dei costi;
- risorse inutilizzate: la H100 resta ferma durante s3, i 48 vCPU quasi inattivi durante s4;
- tempi in parte stimati o ricavati da un singolo batch.

---

## 4. Metodologia

In questo capitolo descrivo come ho impostato le misure, cioè quali configurazioni ho confrontato, su quali dati le ho eseguite, come ho registrato i tempi e quante volte ho ripetuto ogni misura.

### 4.1 Configurazioni

Una configurazione è la combinazione di macchina e dispositivo su cui eseguo la pipeline. Le ho scelte per coprire i casi richiesti dall'estensione, cioè locale e cloud, solo CPU e con GPU. In locale uso il mio Mac mini, mentre sul cloud uso tre VM di Chameleon di dimensione crescente. L'ID di ogni configurazione è lo stesso che compare nella cartella dei risultati (`results/<config>/`) e nelle tabelle del capitolo 7.

| ID | Dove | Risorse |
|---|---|---|
| `local-cpu` | Mac mini M4 (2024) | CPU 10 core (4 performance + 6 efficiency), 16 GB; s3 senza OCR |
| `local-cpu-ocr` | Mac mini M4 (2024) | come sopra, s3 con OCR (Tesseract installato) |
| `local-mps` | Mac mini M4 (2024) | GPU integrata 10 core via Metal (MPS) |
| `cloud-cpu-s` | Chameleon KVM@TACC, flavor `m1.xlarge` | 8 vCPU Intel Haswell, 16 GB |
| `cloud-cpu-l` | Chameleon KVM@TACC, flavor `m1.xxlarge` | 16 vCPU Intel Haswell, 32 GB; creata con l'IaC |
| `cloud-gpu` | Chameleon KVM@TACC, flavor `g1.h100.pci.1` | 1× H100 94 GB, 48 vCPU Xeon Platinum 8468, 250 GB |
| `cloud-gpu-full` | come `cloud-gpu` | corpus completo, una esecuzione |
| `cloud-split` | `cloud-cpu-l` per s3 + `cloud-gpu` per s4 | calcolata dai tempi per fase (§7.6) |
| `local-cpu-ocr-1t` | Mac mini M4 (2024) | come `local-cpu-ocr`, con un thread per sessione di onnxruntime (§7.1.1) |
| `local-cpu-v2`, `local-mps-v2`, `cloud-cpu-s-v2`, `cloud-cpu-l-v2`, `cloud-gpu-v2` | come senza suffisso | stesse misure ripetute con l'ambiente allineato (§4.4) |
| `split-cpu` | come `cloud-cpu-l` | s1, s2 e s3 sul corpus completo con 16 worker |
| `split-gpu` | come `cloud-gpu` | s4 sul corpus completo con i Markdown di `split-cpu`, poi s5 |
| `emb-gpu-bge-m3`, `emb-gpu-embeddinggemma-2` | come `cloud-gpu` | s4 e s5 sul campione già convertito, con bge-m3 o con EmbeddingGemma 2 (§7.9) |
| `emb-gpu-full-bge-m3`, `emb-gpu-full-embeddinggemma-2` | come `cloud-gpu` | come sopra sul corpus completo; per bge-m3 l'indice è quello di `split-gpu` |

Nel piano iniziale avevo previsto 48 vCPU per `cloud-cpu-l`, ma su KVM@TACC il flavor con solo CPU più grande è `m1.xxlarge`, che ne ha 16 (§5.1). Lo scaling di s3 fino a 48 processi è stato misurato sulla VM GPU, che ha 48 vCPU ma un processore diverso (§7.2, §9).

### 4.2 Campione fisso

Ripetere più volte la pipeline sul corpus completo per ogni configurazione non è sostenibile, perché richiederebbe oltre 80 h di sola indicizzazione sulla CPU locale (§7.7) e ogni run ricontatterebbe AIFA per 25.000 download. Per questo ho deciso di usare un **campione fisso**, creato una volta con `bench/make_sample.sh 500`:
- i primi 500 farmaci in ordine di `COD_FARMACO` (ordinamento esplicito, quindi campione stabile);
- 1.000 PDF richiesti, **972 validi**, che producono **31.905 chunk** senza OCR e **31.365** con OCR (§9);
- archiviato come `samples/n500.tar.gz` (SQLite con il manifest + PDF, 294 MB) e copiato su ogni VM (§5.3).

All'inizio pensavo di poter ricavare i tempi del corpus completo da quelli del campione con una semplice proporzione, in base al numero di documenti per s3 e al numero di chunk per s4. Il full run del 28/10 mi ha permesso di verificarlo, e la proporzione **sottostima** i tempi reali del 25–36% perché i documenti del campione sono più brevi di quelli del resto del corpus (§7.5).

### 4.3 Strumentazione

Ogni misura è un run di `bench/run.sh`, che:
1. crea una cartella `results/<config>/<run_id>/` con una copia del database del campione e un collegamento in sola lettura ai PDF, così ogni run parte dallo stesso stato e non tocca i dati della baseline;
2. esegue le fasi richieste registrando inizio, fine e durata in `phases.csv`, insieme a commit, host, numero di CPU, worker, device e dimensione del batch;
3. su Linux registra in background l'utilizzo di CPU (`vmstat`, ogni secondo) e GPU (`nvidia-smi`, ogni secondo).

`bench/collect.py` unisce tutti i run in `results/all.csv`, aggiungendo campione, documenti convertiti e chunk indicizzati letti dalla cartella di ogni run. I tempi di infrastruttura (lease, creazione, configurazione, distruzione) sono scritti dal Makefile in `results/infra_times.csv` (§5.4).

### 4.4 Protocollo

Per ottenere i dati, ho eseguito il seguente protocollo:
- 3 ripetizioni per misura, si riporta la mediana e lo scarto (massimo − minimo) in percentuale della mediana; le eccezioni (misure singole) sono indicate;
- commit registrato in ogni riga di `phases.csv`;
- versioni bloccate in `rag/requirements.txt`; l'interprete e PyTorch dipendono dalla macchina: Python 3.13.4 e PyTorch 2.13.0 in locale, Python 3.12.3 e PyTorch 2.14.1 su `cloud-cpu-s` e `cloud-gpu` (build CPU e CUDA 12.6), PyTorch 2.15.0 su `cloud-cpu-l` (§9);
- macchina locale con la sospensione di sistema disattivata (`caffeinate -i`);
- s4 su CPU e su MPS misurata con una sola ripetizione per la durata (da 2,4 h a 6,9 h); per run così lunghi la variabilità relativa attesa è bassa, e sulle VM cloud lo scarto tra ripetizioni è rimasto sotto l'1% per s4 (§7.3).

Dopo la prima tornata di misure ho allineato gli ambienti e ho ripetuto tutte le misure, nelle configurazioni con suffisso `-v2`. Ora ogni macchina usa Python 3.13.4, installato con uv [17], e PyTorch 2.14.1 nella build per CPU, CUDA 12.6 o macOS. Tutte le altre dipendenze sono fissate in `rag/constraints.txt`, generato dal virtualenv del Mac, quindi le librerie che convertono e indicizzano i documenti hanno la stessa versione ovunque. Le misure `-v2` sono partite tutte dallo stesso commit tra il 30 e il 31/10, con le VM in parallelo.

---

## 5. Provisioning: dalla procedura manuale all'infrastruttura come codice

In questo capitolo racconto come ho creato le VM su Chameleon, prima a mano dalla dashboard e poi con l'infrastruttura come codice. Con infrastruttura come codice si intende descrivere le risorse cloud in file di testo versionati, che un comando trasforma in risorse reali al posto dei clic sulla dashboard. In questo modo la stessa VM si può ricreare identica tutte le volte che serve, e distruggere senza dimenticare niente.

Per prima cosa descrivo le risorse disponibili sul sito KVM@TACC, poi la procedura manuale con i tempi che ho rilevato e il modo in cui ho portato codice e dati sulla VM e i risultati indietro. Infine spiego come ho automatizzato l'intero ciclo di vita della VM con Terraform e un Makefile, e confronto i tempi delle due procedure.

### 5.1 Risorse su Chameleon KVM@TACC

Le misure cloud usano il progetto Chameleon *BioG-LLM* (CHI-261565), con un'allocazione di 20.000 *Service Unit* (SU) valida fino al 26/01/2027 (screenshot in §5.2). Su KVM@TACC ogni istanza richiede un lease di Blazar [15], il servizio di prenotazione di OpenStack, che prenota un *flavor*, cioè un tipo di VM con un numero fisso di vCPU, una quantità di RAM ed eventualmente una GPU. Quando il lease diventa `ACTIVE` compare un flavor dedicato chiamato `reservation:<id>`, il cui id coincide con quello della prenotazione. Le SU si consumano per tutta la durata del lease, non solo mentre la VM lavora: conviene quindi cancellarlo appena finite le misure.

Flavor disponibili rilevanti:

| Flavor | vCPU | RAM | Acceleratore | Uso |
|---|---|---|---|---|
| `m1.xlarge` | 8 | 16 GB | — | `cloud-cpu-s` |
| `m1.xxlarge` | 16 | 32 GB | — | `cloud-cpu-l` (il più grande solo CPU) |
| `g1.h100.pci.1` | 48 | 250 GB | 1× H100 | `cloud-gpu` |
| `g1.h100.vgpu.1g.12gb` | 6 | 36 GB | 1/7 di H100 (12 GB) | non usato |

### 5.2 Procedura manuale

Le VM `cloud-cpu-s` (26/10/2026) e `cloud-gpu` (28/10/2026) sono state create dalla dashboard
(OpenStack Horizon), documentando ogni passo con uno screenshot. Negli screenshot le risorse di altri membri del progetto sono coperte in grigio.

**1. Progetto e allocazione.** Si seleziona il progetto con un'allocazione attiva. Il progetto del corso non ha un'allocazione e quindi non permette di creare risorse.

![Elenco dei progetti Chameleon](img/chameleon/00-progetti.png)
*Elenco dei progetti a cui ho accesso.*

![Allocazione del progetto BioG-LLM](img/chameleon/01-allocazione-biog-llm.png)
*L'allocazione del progetto BioG-LLM, con 20.000 SU fino al 26/01/2027.*

![Progetto del corso senza allocazione](img/chameleon/01a-ccc2425-senza-allocazione.png)
*Il progetto del corso, senza allocazione.*

**2. Accesso.** Servono una coppia di chiavi SSH e un security group con la porta 22 aperta.

![Coppia di chiavi SSH](img/chameleon/03-keypair.png)
*La coppia di chiavi SSH registrata su Chameleon.*

![Security group con la porta 22](img/chameleon/04-security-group.png)
*Il security group con la regola per la porta 22.*

**3. Lease.** Per la H100 si controlla prima la disponibilità nel calendario dei flavor, poi si crea il lease con *Create Lease* scegliendo il flavor, senza prenotare reti. Il lease resta in `PENDING` finché non arriva l'ora di inizio, poi diventa `ACTIVE`.

![Calendario dei flavor H100](img/chameleon/14-flavor-calendar-h100.png)
*Il calendario di disponibilità delle H100, da controllare prima di prenotare.*

![Creazione del lease, scelta del flavor](img/chameleon/05-lease-flavor.png)
*Creazione del lease, scheda del flavor.*

![Creazione del lease, reti](img/chameleon/05b-lease-reti.png)
*Creazione del lease, scheda delle reti, lasciata vuota.*

![Lease in PENDING](img/chameleon/06a-lease-pending.png)
*Il lease della VM `cloud-cpu-s` in attesa dell'ora di inizio.*

![Lease della GPU in PENDING](img/chameleon/15-lease-gpu-pending.png)
*Il lease della VM `cloud-gpu` in attesa dell'ora di inizio.*

![Lease ACTIVE](img/chameleon/06-lease-active.png)
*Il lease diventato `ACTIVE`.*

**4. Istanza.** Con *Avvia istanza* si sceglie nome, immagine `CC-Ubuntu24.04` (`CC-Ubuntu24.04-CUDA` per la GPU), flavor `reservation:<id>`, rete `sharednet1`, coppia di chiavi e security group.

![Dettagli dell'istanza](img/chameleon/07a-istanza-dettagli.png)
*Avvio dell'istanza, scheda dei dettagli.*

![Immagine dell'istanza](img/chameleon/07-istanza-origine.png)
*Avvio dell'istanza, scelta dell'immagine.*

![Istanza CPU attiva](img/chameleon/09-istanza-attiva.png)
*L'istanza `cloud-cpu-s` attiva.*

![Istanza GPU attiva](img/chameleon/16-istanza-gpu-attiva.png)
*L'istanza `cloud-gpu` attiva.*

**5. IP pubblico.** Si alloca un floating IP dal pool `public` e lo si associa alla porta della VM.

![Floating IP della VM CPU](img/chameleon/10-floating-ip.png)
*Il floating IP associato alla VM `cloud-cpu-s`.*

![Floating IP della VM GPU](img/chameleon/17-floating-ip-gpu.png)
*Il floating IP associato alla VM `cloud-gpu`.*

**6. Accesso SSH.** Ci si collega con la chiave privata e l'utente `cc`.

Tempi rilevati (orari dagli screenshot e dal terminale):

| Passo | `cloud-cpu-s` (26/10) | `cloud-gpu` (28/10) |
|---|---|---|
| creazione lease | 18:42 | ~16:53 |
| lease `ACTIVE` | 18:43 | entro le 16:57 |
| istanza attiva | ~18:47 | 16:57 |
| floating IP associato | 18:48 | 16:58 |
| primo SSH | 18:49:19 | 16:59:28 |
| **tempo fino alla VM raggiungibile** | **~7 min** | **~6,5 min** |

### 5.3 Codice e dati: verso la VM e ritorno

Una VM appena creata contiene solo il sistema operativo. Prima di poter effettuare le misurazioni bisogna portarci il codice, il campione e l'ambiente Python, e alla fine riportare i risultati sul Mac. Di seguito descrivo come ho gestito ciascun passaggio.

#### 5.3.1 Codice

Il codice è pubblico su GitHub ([BeastOfShadow/aifa-rag-cloud](https://github.com/BeastOfShadow/aifa-rag-cloud)), ma durante le misure il repository era privato. Per questo nella procedura manuale a ogni VM è stata associata una *deploy key* GitHub in sola lettura, poi rimossa a fine lavoro, e il codice è stato clonato con `git clone` (2 s). La procedura automatica (§5.4) usa invece un `git bundle`: il commit corrente viene impacchettato in locale (9,8 MB) e inviato via SSH, così sulla VM non serve alcuna credenziale e il codice corrisponde esattamente al commit registrato in `phases.csv`.

#### 5.3.2 Campione

Nella procedura manuale `samples/n500.tar.gz` (294 MB) è copiato con `scp` (26–28 s, ~11 MB/s fino a TACC, in Texas), mentre in quella automatica viene inviato come flusso `tar` via SSH ed estratto direttamente sulla VM.

#### 5.3.3 Ambiente

`chameleon/bootstrap.sh` installa i pacchetti di sistema (compreso Tesseract con la lingua italiana), crea il virtualenv e installa PyTorch nella build CPU o CUDA a seconda che sia presente `nvidia-smi` (~160 s). Per le misure `-v2` `bootstrap.sh` installa uv, che scarica Python 3.13.4 e installa PyTorch e le altre dipendenze con le versioni di `constraints.txt` (§4.4). uv scarica e installa i pacchetti in parallelo, e la configurazione della VM scende da 246 s a circa 145 s (§5.5). Il modello bge-m3 viene scaricato prima delle misure (~20 s), così il primo run di s4 non ne include il download.

#### 5.3.4 Esecuzione

I run lunghi girano in `tmux`, che sopravvive alla disconnessione SSH e permette di ricollegarsi per seguire l'avanzamento dei processi.

#### 5.3.5 Ritorno dei risultati

I risultati tornano sul Mac con `rsync` in due passate, prima tutto tranne le cartelle `data/` dei run, poi da queste solo il database SQLite e il collegamento ai PDF, che servono a `collect.py`. Markdown e indici Chroma dei run sul campione sono riproducibili e restano sulla VM. Volumi: 1,4 GB in 113 s (`cloud-cpu-s`, 13 run), 1,9 GB in 162 s (`cloud-gpu`, 17 run).

#### 5.3.6 Full run

Il disco di sistema della VM GPU (40 GB, di cui ~9 GB liberi dopo l'installazione di CUDA e PyTorch) non bastava per il corpus completo. È stato creato un **volume a blocchi** Cinder da 50 GB, collegato alla VM, formattato ext4 e montato in `/mnt/vol` (~2 minuti in tutto).

![Creazione del volume Cinder](img/chameleon/18-volume-crea.png)
*Creazione del volume da 50 GB per il full run.*

Occupazione finale: PDF 6,1 GB, Markdown 1,1 GB, Chroma 12 GB, SQLite 115 MB, quindi ~19 GB, oltre il disco di sistema disponibile. Sul Mac sono tornati solo tempi, log e database (306 MB). Nella seconda tornata il volume lo crea Terraform insieme alla VM (§5.4).

#### 5.3.7 Pulizia

Floating IP e volume **non** vengono rilasciati alla scadenza o alla cancellazione del lease: dopo il run GPU sono rimasti allocati e sono stati eliminati a mano. Con la procedura automatica questo non succede, perché Terraform rilascia il floating IP e distrugge il volume con `make down`.

### 5.4 Infrastruttura come codice

Una volta create a mano le prime due VM, ho proceduto con l'automatizzazione dell'intero ciclo di vita della macchina, con parametri configurabili come il flavor, il nome del lease e la configurazione da misurare. Il ciclo di vita della VM è descritto da due file versionati, [infra/main.tf](../infra/main.tf) e il [Makefile](../Makefile). I comandi che possono essere lanciati dal Makefile sono:

| Comando | Strumento | Cosa fa |
|---|---|---|
| `make lease` | CLI OpenStack + Blazar [15, 16] | prenota il flavor e attende che il lease sia `ACTIVE` |
| `make up` | Terraform [12] (`infra/main.tf`) | crea security group, porta, istanza e floating IP, poi attende che SSH risponda |
| `make deploy` | `git bundle` + `tar` via SSH | copia codice e campione, esegue `bootstrap.sh`, scarica il modello bge-m3 |
| `make bench` | `tmux` | avvia il piano di misure `bench/plan_<config>.sh` |
| `make fetch` | `rsync` | copia i risultati in `results/<config>/` |
| `make fetch-md` | `rsync` | configurazione split, VM CPU: copia anche Markdown e database del corpus completo |
| `make push-md` | `rsync` | configurazione split, VM GPU: invia i Markdown e il database ricevuti dalla VM CPU |
| `make down` | Terraform + CLI OpenStack | distrugge VM, porta, security group, IP, volume e lease |

Scelte di progetto:
- **Lease fuori da Terraform.** Il provider OpenStack di Terraform copre i servizi standard (Nova, Neutron, Cinder) ma non Blazar, che è un'aggiunta di Chameleon. Il lease è quindi creato con la CLI `openstack reservation` e Terraform riceve solo l'id della prenotazione, che su KVM è anche l'id del flavor. L'infrastruttura è in parte **dichiarativa** (Terraform: stato desiderato, `plan`, `apply`, `destroy`) e in parte **imperativa** (comandi CLI); il Makefile le unisce. La libreria python-chi [13] avrebbe fatto lo stesso con più dipendenze.
- **Niente cloud-init.** cloud-init [14] avrebbe solo installato i pacchetti di sistema, che `bootstrap.sh` installa già in ~20 s; senza credenziali non può scaricare il codice. Il Makefile aspetta comunque la fine di cloud-init (`cloud-init status --wait`) prima di usare `apt`.
- **Security group dedicato** con la sola porta 22: quello condiviso nel progetto apre anche le porte 80, 6443 e 11434. Viene creato e distrutto insieme alla VM.
- **Porta esplicita.** La VM si collega alla rete tramite una porta creata da Terraform, a cui si associa il floating IP: allocazione e associazione avvengono in un'unica risorsa, e `destroy` rilascia l'IP.
- **Chiavi host SSH non verificate.** Ogni VM ha una chiave host nuova e gli IP vengono riassegnati, quindi la verifica su `known_hosts` è disattivata. È accettabile per VM usa-e-getta raggiungibili solo con la chiave privata, non per macchine di lunga durata.
- **Controlli.** `make up` si ferma prima di Terraform se il lease non esiste, per non lasciare risorse create a metà; `make deploy` si ferma se il piano di misure non è nel commit, perché il bundle porta solo i commit.
- **Portabilità.** Il Make di macOS (GNU Make 3.81) non supporta `.ONESHELL`, quindi ogni ricetta è un'unica riga logica; `date -v` per la fine del lease è sintassi BSD (su Linux: `date -d`).
- **Più VM in parallelo.** Terraform tiene lo stato di ogni VM in un *workspace* con il nome del lease, così ogni comando del Makefile lavora sulla VM del suo lease e più VM possono esistere insieme. Nella seconda tornata ho tenuto accese fino a quattro VM nello stesso momento.
- **Volume e immagine come parametri.** Con `VOLUME_GB` Terraform crea un volume Cinder, che `make deploy` formatta e monta in `/mnt/vol` e su cui sposta campioni e risultati. Con `IMAGE` si sceglie l'immagine, per esempio quella con CUDA per la H100.

### 5.5 Confronto dei tempi

Una volta creata l'automazione, ho proceduto con il confronto dei dati da `results/infra_times.csv`. Nella prima tornata ci sono due cicli completi tra il 29 e il 30/10/2026, un collaudo con distruzione immediata e il run `cloud-cpu-l`. Nella seconda, tra il 30 e il 31/10, ho creato con lo stesso Makefile quattro VM CPU (tre insieme) e la VM con la H100, che aveva anche un volume da 30 GB.

| Passo | Manuale | IaC, collaudo | IaC, run `cloud-cpu-l` | IaC, seconda tornata |
|---|---|---|---|---|
| lease `ACTIVE` | ~1–4 min | 27 s | 27 s | 26–28 s |
| VM raggiungibile via SSH | ~6,5–7 min dall'inizio | +42 s (69 s dall'inizio) | +44 s (71 s dall'inizio) | +41–44 s, +85 s per la H100 con il volume |
| configurazione (codice, campione, ambiente, modello) | ~3,7 min di comandi a mano | — | 246 s | 141–152 s (mediana 144 s), 126 s sulla H100 |
| **pronta per le misure** | **~11 min**, più i passaggi manuali | — | **5 min 17 s** | **~3 min 34 s** per una VM CPU |
| recupero dei risultati (`make fetch`) | `rsync` a mano | — | 7 s | 18–184 s, a seconda di quanti run |
| distruzione | a mano, IP e volume dimenticati | 19 s, nessun residuo | 21 s, nessun residuo | 7–31 s, nessun residuo |

Osservazioni:
- Il guadagno è nel provisioning: da ~7 minuti a ~70 secondi, con misure quasi identiche in tutti i cicli, anche con tre VM create nello stesso momento.
- Nella prima tornata la configurazione durava quanto a mano (~4 minuti), perché è dominata dall'installazione di pacchetti e PyTorch, non dai tempi umani. Con uv (§5.3.3) scende a circa 2,4 minuti. Il passo successivo sarebbe un'immagine personalizzata con l'ambiente già installato.
- Il volume della H100 aggiunge circa 40 s alla creazione, ma senza di esso il disco non basta per il corpus completo (§5.3.6).
- Rispetto al lavoro utile, l'overhead pagato (`make up` + `make deploy`, §6) nella prima tornata era di 290 s e valeva il 2,4% del full run sulla GPU (3,3 h di s3 + s4), ma quasi quanto l'intero run sul campione (6,2 minuti). Nella seconda è di 187 s per una VM CPU e di 211 s per la VM GPU, e pesa l'1,8% sul full run. È l'ingrediente dell'ipotesi H3 (§7.7, §8).

### 5.6 Cosa è andato storto nella seconda tornata

Per la seconda tornata (§4.4) ho fatto partire le misure la sera, con degli script sul Mac che ogni 10 minuti controllavano se il piano di una VM era finito, scaricavano i risultati e solo dopo distruggevano la VM. Cinque errori mi hanno fatto perdere qualche ora, e li riporto perché dicono qualcosa su come si lavora con il cloud.

- **Markdown nel posto sbagliato.** Per la configurazione split ho modificato `run.sh` in modo che usasse i Markdown già convertiti, se presenti nella cartella del campione. Il controllo però guardava solo se la cartella esisteva, e il campione ne aveva una vuota. Il primo run sul Mac ha quindi scritto i suoi Markdown nel campione condiviso, e i run successivi li hanno trovati già pronti, durando 0 secondi. Me ne sono accorto dai tempi, ho cancellato quei run, ho corretto `run.sh` (commit `fc80997`) e ho ripetuto la serie.
- **VM cancellata alla scadenza del lease.** Lo script che controllava `split-cpu` cercava l'IP nello stato di Terraform senza indicare il workspace (§5.4), quindi non ha mai visto la fine del piano. Alla scadenza del lease Chameleon ha distrutto la VM insieme ai PDF e ai Markdown del corpus completo, che non avevo ancora scaricato. Alla fine di un lease la VM sparisce senza avviso, quindi i risultati vanno scaricati prima, e il lease va prenotato con un margine o prorogato (`openstack reservation lease set --prolong-for`).
- **Lease duplicati.** Lo script che creava le due VM `-v2` passava i parametri a make dentro una variabile di zsh, che a differenza di bash non la divide in parole. make ha usato il nome di lease predefinito per entrambe le VM, ha creato due lease con lo stesso nome e poi è rimasto bloccato tutta la notte, perché la CLI non sapeva quale dei due scegliere. La mattina ho rinominato e prorogato i lease, così le prenotazioni non sono andate perse.
- **Secondo deploy sulla stessa VM.** Sulla H100 ho eseguito prima `cloud-gpu-v2` e poi `split-gpu`, quindi la seconda configurazione ha ripetuto `make deploy` su una VM già pronta. Il nuovo `bootstrap.sh` crea il virtualenv con uv, che si ferma se il virtualenv esiste già. Il codice era già allo stesso commit e l'ambiente era già installato, quindi ho saltato il deploy, e poi ho corretto `bootstrap.sh` perché riusi il virtualenv esistente.
- **Cartella dei PDF mancante.** Sulla VM GPU della configurazione split arrivano solo i Markdown e il database, non i PDF. `run.sh` però collega comunque la cartella dei PDF del campione, e `config.py` si ferma se trova un collegamento a una cartella che non esiste. Sul Mac non era emerso perché i campioni su cui avevo provato avevano i PDF. Ho creato la cartella vuota sulla VM e ho corretto `make push-md`, che ora la crea da sé.

In tutti e cinque i casi l'infrastruttura come codice ha reso il recupero veloce, perché ricreare e configurare una VM richiede pochi minuti. Il costo è stato una notte di SU su tre lease, due fermi e uno con il lavoro perso, e qualche ora di ritardo sulla configurazione split.

---

## 6. Modello di costo

Chameleon è gratuito per la ricerca e la didattica, quindi il costo in euro è **stimato** con listini commerciali di risorse equivalenti. Tutti i prezzi sono IVA inclusa (22%), dal punto di vista di chi paga davvero.

```
costo_cloud  = Σ_VM (ore_fasi_sulla_VM + ore_overhead) × prezzo_orario_equivalente
costo_locale = ore × (ammortamento_orario + potenza_kW × prezzo_kWh)
```

Il costo si calcola sulla **durata del processo più l'overhead** di provisioning e configurazione (§5.5), non sulla durata del lease: su un cloud commerciale si paga l'istanza accesa e la si spegne a fine lavoro, e con l'automazione lo spegnimento è immediato. La durata del lease conta invece per le SU di Chameleon, che non hanno un prezzo in euro.

L'overhead per VM cloud è il tempo di `make up` + `make deploy`. Nella prima tornata l'ho misurato il 29/10/2026 su una VM CPU, (44 + 246) s = **0,0806 h**, nella colonna `overhead_h` di `bench/prices.csv`, e l'ho assunto uguale per la VM GPU, dove la configurazione manuale aveva durata simile (§5.3). Nella seconda tornata l'ho misurato separatamente per i due tipi di VM, con le mediane di `results/infra_times.csv`: (42,5 + 144) s = **0,0518 h** per una VM CPU e (85 + 126) s = **0,0586 h** per la VM GPU, nella colonna `overhead_v2_h`. I costi di ogni tornata usano il suo overhead. Il lease di Chameleon non ha equivalente nei cloud commerciali e non è contato. La configurazione eterogenea usa due VM e paga l'overhead due volte; in locale l'overhead è nullo.

### 6.1 Costo orario della macchina locale

Riassunto dei costi della macchina locale (Mac mini M4):

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

Ipotesi da dichiarare:
- l'ammortamento su 24 h al giorno è il caso più favorevole al locale. Con un uso effettivo minore il costo orario cresce;
- 65 W è il consumo massimo con CPU e GPU sature: è un limite superiore, il consumo reale durante s3 e s4 è probabilmente più basso;
- 0,4343 €/kWh è la tariffa di riferimento pubblica per i clienti vulnerabili, scelta perché verificabile (la tariffa di mercato libero può differire).

### 6.2 Listini cloud

Prezzi on-demand rilevati il 21 e il 27/10/2026, in [bench/prices.csv](../bench/prices.csv) con fonte e data per ogni riga. I prezzi in dollari sono convertiti al cambio BCE del 21/10/2026 (1 EUR = 1,1234 USD); a tutti è aggiunta l'IVA del 22%.

| Classe | Fornitore | Istanza | €/h IVA incl. | Note |
|---|---|---|---|---|
| 1× H100 | Azure | NC40ads_H100_v5 (40 vCPU, 320 GiB, H100 NVL 94 GB) | 9,7416 | listino in euro, Germany West Central |
| 1× H100 | AWS | p5.4xlarge (16 vCPU, 256 GiB, H100 80 GB) | 7,9946 | Stoccolma; non offerta a Francoforte |
| 1× H100 | Lambda | 1x H100 PCIe (26 vCPU, 225 GiB, 80 GB) | 3,5729 | regione non indicata |
| 1× H100 | RunPod | H100 NVL Secure Cloud (16 vCPU, 94 GB) | 3,4643 | *pod* (container), non VM |
| 16 vCPU | Azure | F16s_v2 (32 GiB) | 0,8331 | Germany West Central |
| 16 vCPU | AWS | c7i.4xlarge (32 GiB) | 0,8849 | Francoforte |
| 16 vCPU | GCP | c4-standard-16 (60 GiB) | 1,0132 | Francoforte |
| 8 vCPU | Azure | F8s_v2 (16 GiB) | 0,4165 | Germany West Central |
| 8 vCPU | AWS | c7i.2xlarge (16 GiB) | 0,4424 | Francoforte |
| 8 vCPU | GCP | c3-standard-8 (32 GiB) | 0,5167 | Francoforte |

Per GCP una VM con una sola H100 (a3-highgpu-1g) non è disponibile on-demand. Il divario tra fornitori per la stessa GPU è di quasi tre volte: la scelta del fornitore pesa più di quella della configurazione (§7.6).

---

## 7. Risultati

In questo capitolo presento i risultati delle misure. Parto dalla conversione dei PDF (s3), prima sul Mac e poi sulle VM cloud, per capire come scala all'aumentare dei worker. Poi passo all'embedding (s4) sui diversi dispositivi, al full run sul corpus completo e al confronto tra campione e corpus. Infine metto insieme tempi e prezzi per calcolare il costo di ogni configurazione e il punto di pareggio tra locale e cloud. Chiudo con il tempo di risposta a una domanda (s5) e con il confronto tra due modelli di embedding. Dove non è indicato diversamente, le misure sono fatte sul campione fisso di 972 documenti (§4.2).

Le misure sono arrivate in due tornate. La prima l'ho eseguita tra il 21 e il 30/10, con versioni di Python e PyTorch diverse da macchina a macchina. Analizzando i risultati ho scoperto perché il Mac non scalava (§7.1.1), e a quel punto ho allineato gli ambienti (§4.4) e ho ripetuto tutte le misure tra il 30 e il 31/10. In questa seconda tornata ho anche eseguito davvero la configurazione eterogenea, che nella prima avevo solo calcolato, e ho misurato s5 sul cloud. Nelle tabelle riporto entrambe le tornate, così si vede quanto cambia ogni misura, mentre i costi e le conclusioni si basano sulla seconda.

Per descrivere lo scaling uso due grandezze. Lo *speedup* dice quante volte un run con *p* worker è più veloce dello stesso run con un solo worker. L'*efficienza* divide lo speedup per il numero di worker e indica quanto viene sfruttato ogni worker aggiunto. Vale 1 se raddoppiando i worker il tempo si dimezza, e scende quando una parte del lavoro non si riesce a dividere tra i worker [6]. In formule, con T(p) il tempo con *p* worker, `S(p) = T(1) / T(p)` e `E(p) = S(p) / p`.

### 7.1 s3: scaling della conversione da PDF a Markdown in locale

`local-cpu-ocr` (con OCR, confrontabile con il cloud), 3 ripetizioni:

| Worker *p* | Tempi (s) | Mediana (s) | Scarto | Doc/s | Speedup S(p) | E(p) |
|---|---|---|---|---|---|---|
| 1 | 1215 · 1208 · 1282 | 1215 | 6,1% | 0,80 | 1,00× | 1,00 |
| 2 | 879 · 886 · 876 | 879 | 1,1% | 1,11 | 1,38× | 0,69 |
| 4 | 1113 · 938 · 1128 | 1113 | 17,1% | 0,87 | 1,09× | 0,27 |
| 8 | 1054 · 1368 · 1058 | 1058 | 29,7% | 0,92 | 1,15× | 0,14 |
| 10 | 1127 · 1144 · 1144 | 1144 | 1,5% | 0,85 | 1,06× | 0,11 |

La serie senza OCR (`local-cpu`) ha lo stesso andamento: 1115 s con 1 worker, minimo di 688 s con 2 (S = 1,62), 1071 s con 10, con scarti sotto il 3,5%. L'OCR costa sul Mac solo il 9–28% in più per documento.

Osservazioni:
- Lo speedup si ferma a 2 worker e oltre **peggiora**: con 10 worker il tempo torna quasi quello seriale.
- La legge di Amdahl [6] non spiega questo andamento: Amdahl non prevede mai un rallentamento all'aumentare dei core, quindi il calo indica contesa su una risorsa condivisa.
- Ipotesi: 
  
  a. contesa tra i processi e i thread interni delle librerie (*oversubscription*);
  
  b. i 6 core efficiency del M4 sono più lenti e diventano il collo di bottiglia;
  
  c. banda di memoria condivisa tra CPU e GPU. La stessa pipeline sulle VM cloud scala quasi linearmente (§7.2), quindi all'inizio pensavo che il limite fosse della macchina e non del codice.
- Gli scarti alti con 4 e 8 worker (17–30%) sono dovuti all'uso del Mac durante quelle serie; la mediana ne attenua l'effetto.

Configurazione locale migliore in questa prima serie: 2 worker, 879 s.

#### 7.1.1 Perché il Mac non scalava

Il risultato non mi convinceva, perché la stessa pipeline sulle VM scala quasi linearmente. Per questo ho verificato le ipotesi una alla volta, con piccoli test su 240 PDF del campione.

**Calcolo puro.** Ho eseguito 40 operazioni identiche di solo calcolo, senza leggere file e con pochissima memoria, aumentando i processi da 1 a 10. Con 10 processi il tempo scende di 5,3 volte, quindi la CPU del Mac scala. I core efficiency limitano lo speedup massimo (ipotesi b), ma non spiegano un rallentamento.

**Memoria.** Ogni worker di s3 occupa circa 660 MB, e durante la prima serie il Mac aveva altre applicazioni aperte e usava già 13,6 GB di swap su 14,3. Ho convertito i 240 PDF registrando con `vm_stat` quante pagine di memoria comprimeva macOS. Con le applicazioni aperte le compressioni passano da 2,3 milioni con 1 worker a 35 milioni con 10, e con 10 worker il tempo è peggiore che con 1 (speedup 0,89). Chiudendo le applicazioni le compressioni spariscono e lo speedup sale a 1,37, ma resta lontano da quello delle VM. La memoria era quindi una parte del problema, non tutto.

**Thread interni.** pymupdf4llm 1.28 attiva da solo PyMuPDF Layout, che riconosce la struttura della pagina con modelli ONNX eseguiti da onnxruntime. Ogni sessione di onnxruntime usa di default più thread [18]. Sul Mac un worker occupava circa 3 core (85 s di CPU in 28 s di tempo reale), in gran parte in attesa attiva, mentre sulle VM Linux ne usava uno solo. Con 10 worker c'erano quindi circa 30 thread che si contendevano 10 core, cioè proprio l'ipotesi a.

La correzione è in `rag/s3_pdf_to_md.py`. Prima di importare pymupdf4llm forzo un solo thread per ogni sessione di onnxruntime, così il parallelismo lo danno solo i worker (commit `59c93a9`).

| Test sui 240 PDF con 10 worker | Tempo (s) | Speedup | Compressioni di memoria |
|---|---|---|---|
| applicazioni aperte | 220,8 | 0,89× | 35 milioni |
| memoria libera | 132,3 | 1,37× | 0,1 milioni |
| memoria libera e un thread per sessione | 57,7 | 3,40× | 0,1 milioni |

#### 7.1.2 La serie ripetuta dopo la correzione

Con la correzione e l'ambiente allineato (§4.4) ho ripetuto la serie sul campione, con il Mac libero da altre applicazioni (`local-cpu-ocr-1t`, 3 ripetizioni):

| Worker *p* | Tempi (s) | Mediana (s) | Scarto | Doc/s | Speedup S(p) | E(p) |
|---|---|---|---|---|---|---|
| 1 | 1124 · 1121 · 1114 | 1121 | 0,9% | 0,87 | 1,00× | 1,00 |
| 2 | 631 · 634 · 630 | 631 | 0,6% | 1,54 | 1,78× | 0,89 |
| 4 | 394 · 395 · 395 | 395 | 0,3% | 2,46 | 2,84× | 0,71 |
| 8 | 315 · 315 · 314 | 315 | 0,3% | 3,09 | 3,56× | 0,44 |
| 10 | 308 · 307 · 308 | 308 | 0,3% | 3,16 | 3,64× | 0,36 |

Osservazioni:
- Ora lo speedup cresce fino a 10 worker, e con 10 worker il tempo è 3,7 volte più basso di quello della prima serie (308 s contro 1144 s).
- Fino a 4 worker l'efficienza resta alta, poi cala. Oltre i 4 core performance i worker finiscono sui 6 core efficiency, più lenti, e da 8 a 10 worker si guadagna solo il 2%. Questa è la parte dell'ipotesi b che resta vera.
- Con 10 worker il Mac converte il campione in 308 s, quanto la VM da 16 vCPU (318 s, §7.2).
- Sulle VM la correzione non cambia i tempi (48 worker sulla VM GPU: 90 s prima e dopo), perché lì onnxruntime usava già un solo core.
- Gli scarti sono tutti sotto l'1%, contro il 17–30% della prima serie, perché questa volta il Mac non era usato durante le misure.

Configurazione locale migliore: 10 worker, 308 s.

### 7.2 s3: scaling sulle VM cloud

Sulle tre VM cloud ho misurato s3 aumentando ogni volta il numero di worker fino al numero di vCPU disponibili. Le VM sono `cloud-cpu-s` con 8 vCPU Haswell, `cloud-cpu-l` con 16 vCPU Haswell, creata con l'IaC, e la VM `cloud-gpu` con 48 vCPU Sapphire Rapids. Quest'ultima l'ho usata anche per s3, lasciando ferma la GPU, perché è l'unica con più di 16 vCPU e permette di vedere cosa succede oltre quella soglia.

La serie l'ho eseguita due volte. La prima tornata è quella dal 26 al 30/10, con le versioni di Python e PyTorch diverse da VM a VM. La seconda l'ho aggiunta il 31/10, dopo aver allineato gli ambienti (§4.4), su VM nuove create con lo stesso Makefile. In tabella della prima tornata riporto solo la mediana, mentre della seconda riporto i tre tempi, e da questi calcolo lo scarto, lo speedup e l'efficienza. L'ultima colonna dice di quanto la seconda tornata è più lenta (+) o più veloce (−) della prima. Ogni misura è ripetuta 3 volte, tranne quella con un solo worker su `cloud-cpu-l` e sulla VM GPU, che è la più lunga e l'ho eseguita una volta sola per tornata.

| VM | *p* | 1ª tornata, mediana (s) | 2ª tornata, tempi (s) | 2ª tornata, mediana (s) | Scarto | Speedup S(p) | E(p) | Differenza |
|---|---|---|---|---|---|---|---|---|
| 8 vCPU | 1 | 4369 | 4483 · 4484 · 4493 | 4484 | 0,2% | 1,00× | 1,00 | +2,6% |
| 8 vCPU | 2 | 2243 | 2283 · 2276 · 2280 | 2280 | 0,3% | 1,97× | 0,98 | +1,6% |
| 8 vCPU | 4 | 1154 | 1171 · 1171 · 1169 | 1171 | 0,2% | 3,83× | 0,96 | +1,5% |
| 8 vCPU | 8 | 595 | 609 · 604 · 608 | 608 | 0,8% | 7,38× | 0,92 | +2,2% |
| 16 vCPU | 1 | 4387 | 4533 | 4533 | — | 1,00× | 1,00 | +3,3% |
| 16 vCPU | 4 | 1154 | 1175 · 1181 · 1179 | 1179 | 0,5% | 3,84× | 0,96 | +2,2% |
| 16 vCPU | 8 | 597 | 612 · 611 · 612 | 612 | 0,2% | 7,41× | 0,93 | +2,5% |
| 16 vCPU | 16 | 319 | 325 · 326 · 326 | 326 | 0,3% | 13,90× | 0,87 | +2,2% |
| 48 vCPU | 1 | 2478 | 2511 | 2511 | — | 1,00× | 1,00 | +1,3% |
| 48 vCPU | 8 | 338 | 326 · 327 · 331 | 327 | 1,5% | 7,68× | 0,96 | −3,3% |
| 48 vCPU | 16 | 181 | 175 · 175 · 177 | 175 | 1,1% | 14,35× | 0,90 | −3,3% |
| 48 vCPU | 32 | 109 | 108 · 108 · 108 | 108 | 0,0% | 23,25× | 0,73 | −0,9% |
| 48 vCPU | 48 | 91 | 88 · 90 · 90 | 90 | 2,2% | 27,90× | 0,58 | −1,1% |

![Scaling di s3 nella prima tornata](charts/s3_scaling.png)

![Scaling di s3 nella seconda tornata](charts/s3_scaling_v2.png)

Il primo grafico è della prima tornata, il secondo della seconda, dove in grigio resta per confronto la serie del Mac della prima tornata.

Osservazioni:
- Tra le due tornate le VM Haswell sono più lente dell'1,5–3,3%, in modo uniforme su entrambe le VM e con ogni numero di worker, mentre sulla VM GPU le differenze vanno da −3,3% a +1,3%. Non riesco a separare l'effetto dell'ambiente allineato da quello di un nodo fisico diverso (§9), ma in ogni caso lo scaling non cambia e le conclusioni restano le stesse.
- Le VM sono molto stabili. Nella seconda tornata lo scarto tra ripetizioni resta sotto l'1% sulle VM Haswell e arriva al massimo al 2,2% sulla VM GPU con 48 worker.
- **Riproducibilità tra VM.** Le VM da 8 e da 16 vCPU hanno la stessa CPU, e con lo stesso numero di worker danno tempi quasi identici in entrambe le tornate. Nella prima la differenza era al massimo dello 0,4%, tra una VM creata a mano e una creata con Terraform a tre giorni di distanza. Nella seconda è al massimo dell'1,1%, con due VM create insieme.
- Lo scaling è quasi lineare fino a 16 worker, con un'efficienza di 0,87 sulla VM da 16 vCPU e di 0,90 sulla VM GPU. Con 32 e 48 worker l'efficienza scende a 0,73 e 0,58. Con la legge di Amdahl, da S(48) si ricava una frazione seriale di circa l'1,6%, che prevede S(16) ≈ 13,0 e S(32) ≈ 21,5 contro i 14,3 e 23,3 misurati. Oltre alla parte seriale pesano lo sbilanciamento finale, perché con 48 processi ognuno converte circa 20 documenti e pochi documenti lunghi allungano la coda, e il dubbio che oltre una certa soglia le vCPU siano hyperthread invece di core fisici (§9).
- **Velocità per core.** Con un solo worker, nella seconda tornata, un core M4 converte il campione in 1121 s (§7.1.2), un core Sapphire Rapids in 2511 s e un core Haswell in 4484 s, cioè 4 volte più lentamente del M4. Nella prima tornata la VM da 8 vCPU con 8 worker batteva il Mac, che non riusciva a usare i suoi core. Dopo la correzione di s3 il Mac con 10 worker (308 s) è invece 2 volte più veloce della VM da 8 vCPU (608 s) e veloce quanto quella da 16 (326 s).

### 7.3 s4: embedding per device

La fase s4 trasforma i frammenti in vettori con il modello bge-m3 e li salva in ChromaDB. È la fase che si avvantaggia di più di una GPU, perché il modello esegue soprattutto moltiplicazioni di matrici. Ho misurato s4 su tutti i dispositivi a disposizione, cioè la CPU e la GPU integrata del Mac, le due VM solo CPU e la H100. Sui dispositivi più lenti una misura dura da 2,3 a quasi 7 ore, quindi l'ho eseguita una volta sola per tornata, mentre sulla H100 l'ho ripetuta 3 volte. Tutte le misure usano batch 64. Come per s3 riporto le due tornate nella stessa tabella. La velocità in chunk al secondo e il confronto con la CPU del Mac li calcolo sulla seconda tornata, e la colonna "Rispetto alla CPU del Mac" dice quante volte ogni dispositivo è più veloce della CPU del Mac, per cui un valore sotto 1 vuol dire più lento. Nella prima tornata il Mac aveva 31.905 chunk perché usava i Markdown convertiti senza OCR (§9), mentre nella seconda tutte le macchine indicizzano gli stessi 31.365 chunk. Sul corpus completo la seconda tornata è l'indicizzazione di `split-gpu` (§7.6.3).

| Config | Device | Chunk (2ª tornata) | 1ª tornata (s) | 2ª tornata (s) | Differenza | Chunk/s | Rispetto alla CPU del Mac |
|---|---|---|---|---|---|---|---|
| `local-cpu` | CPU M4, 10 core | 31.365 | 8.536 | 8.130 | −4,8% | 3,86 | 1,00× |
| `local-mps` | GPU M4 (MPS) | 31.365 | 24.455 | 10.716 | −56% | 2,93 | 0,76× |
| `cloud-cpu-s` | 8 vCPU Haswell | 31.365 | 24.666 | 23.819 | −3,4% | 1,32 | 0,34× |
| `cloud-cpu-l` | 16 vCPU Haswell | 31.365 | 14.338 | 14.758 | +2,9% | 2,13 | 0,55× |
| `cloud-gpu` | H100 | 31.365 | 281 (281 · 282 · 281) | 277 (275 · 277 · 279) | −1,4% | 113,2 | **29,3×** |
| `cloud-gpu-full` / `split-gpu` | H100 | 807.916 | 9.833 | 9.756 | −0,8% | 82,8 | 21,5× |

Il tempo comprende caricamento del modello, chunking e scrittura in Chroma.

Osservazioni:
- La H100 è circa 29 volte più veloce della CPU del Mac e da 53 a 86 volte più veloce delle VM da 16 e 8 vCPU, con valori quasi uguali nelle due tornate.
- Con la stessa versione di PyTorch su tutte e due le VM, raddoppiare le vCPU accelera s4 di 1,61 volte (efficienza 0,81). Nella prima tornata risultava 1,72, ma la VM da 16 vCPU aveva una versione di PyTorch più recente (§9). Anche così la VM da 16 vCPU resta 1,8 volte più lenta dei 10 core del Mac, quindi su CPU l'embedding del corpus completo non è un'opzione.
- La differenza più grande tra le tornate è la GPU del Mac, che nella seconda impiega meno della metà del tempo. Nella prima tornata il Mac era usato anche per altro e la memoria era spesso al limite (§7.1.1), e nel frattempo è cambiata anche la versione di PyTorch, quindi non so dire quanto pesi ciascuna delle due cose. Anche così la GPU del Mac resta 1,3 volte più lenta della CPU, quindi per s4 in locale si usa ancora la CPU.
- Sulle altre macchine le differenze tra le tornate restano entro il 5%, e sulla H100 entro l'1,4%.

![Tempi di s3 e s4 sul campione nelle due tornate](charts/rounds_times.png)

Il grafico mette a confronto le due tornate. A sinistra c'è s3 con tutti i core di ogni macchina, dove l'unica differenza grande è il Mac dopo la correzione di s3 (§7.1.1). A destra c'è s4, in scala logaritmica perché i tempi vanno da pochi minuti a quasi 7 ore, dove cambia solo la GPU del Mac.

Sulla H100 ho provato anche diverse dimensioni del *batch*, cioè quanti frammenti il modello elabora insieme in un solo passaggio sulla GPU. Di solito un batch più grande sfrutta meglio la GPU, quindi mi aspettavo i tempi più bassi con 256. Le misure con batch 64 e 256 sono ripetute 3 volte, quelle con 32 e 128 una volta sola, in entrambe le tornate.

| Batch | 32 | 64 | 128 | 256 |
|---|---|---|---|---|
| s4 (s), 1ª tornata | 262 | 281 | 315 | 410 |
| s4 (s), 2ª tornata | 259 | 277 | 311 | 405 |
| più veloce del batch 256 (v0), 2ª tornata | 1,56× | 1,46× | 1,30× | 1,00× |

![s4 sulla H100: tempo per dimensione del batch, seconda tornata](charts/s4_batch_v2.png)

Il grafico è della seconda tornata; quello della prima, quasi identico, è in [charts/s4_batch.png](charts/s4_batch.png).

Il risultato è l'opposto, cioè il tempo cresce con il batch. `s4_index.py` passa al modello blocchi di 256 testi, il modello li ordina per lunghezza e li divide in sotto-batch della dimensione richiesta, ciascuno completato con *padding* fino al testo più lungo. Sotto-batch piccoli contengono testi di lunghezza simile e quindi meno padding. Sul corpus completo il vantaggio si riduce molto (§7.5): la scelta del batch fatta sul campione va verificata sul carico reale.

### 7.4 Full run sul corpus completo

Le misure precedenti sono tutte sul campione. Per sapere quanto dura davvero l'elaborazione completa, e per controllare se il campione la rappresenta bene, ho eseguito una volta l'intera pipeline sul corpus completo, dal download all'indicizzazione. L'ho fatto il 28/10/2026 sulla VM `cloud-gpu`, con i dati su un volume Cinder (§5.3.6) e batch 64. È la stessa elaborazione della baseline v0, ma questa volta con i tempi di ogni fase registrati e l'uso di CPU e GPU campionato ogni secondo.

| Fase | Durata | Note |
|---|---|---|
| s1 | 3 s | |
| s2 | 9.587 s (2 h 40 min) | 25.198 PDF richiesti a AIFA dal Texas, 6,1 GB |
| s3 | 2.109 s (35 min) | 17.984 documenti con 48 worker (baseline: 37 min) |
| s4 | 9.833 s (2 h 44 min) | 807.495 chunk, 82 chunk/s (baseline: ~2,5 h osservate, batch 256) |
| **totale** | **21.532 s (5 h 59 min)** | di cui s3 + s4: 11.942 s (3 h 19 min) |

Documenti e chunk sono in linea con la baseline (17.959 documenti, 804.995 chunk): +0,1% e +0,3%, probabilmente per documenti pubblicati o aggiornati da AIFA tra luglio e ottobre.

Per capire se la VM era sfruttata bene, durante il run ho registrato ogni secondo quanto lavoravano la CPU e la GPU, con `vmstat` per la CPU e `nvidia-smi` per la GPU. La tabella riporta la media di ogni fase. Una percentuale alta vuol dire che la risorsa stava lavorando, una bassa che era ferma mentre la stavo pagando.

| Fase | CPU (48 vCPU) | GPU (H100) | Memoria della GPU |
|---|---|---|---|
| s3, conversione | occupata al 96% | ferma (0%) | non usata |
| s4, embedding | occupata al 3% | occupata all'83% in media | al massimo 39,6 GB su 94 |

Durante s3 la CPU lavora quasi sempre (del 96%, il 27% è tempo speso dal sistema operativo) e la GPU è ferma. Durante s4 succede il contrario, la GPU lavora e la CPU è quasi inattiva. Anche in s4 la GPU non è sempre al lavoro, perché in circa un secondo su dieci (l'8% dei campioni) è ferma, probabilmente mentre il programma prepara il blocco successivo di testi o scrive i vettori in Chroma.

Nella configurazione monolitica la H100 lavora solo durante s4: download e conversione occupano il **54%** del tempo totale (3 h 15 min su 5 h 59 min) con la GPU ferma, e durante s4 restano inattive le 48 vCPU. È la misura diretta dello spreco che la configurazione eterogenea vuole eliminare (H1).

### 7.5 Campione e corpus completo

Il full run permette di verificare l'estrapolazione lineare dal campione. Le prime due righe sono della prima tornata, sulla VM GPU. Le altre due sono della seconda, con la configurazione eterogenea eseguita (§7.6.3), quindi con s3 sulla VM da 16 vCPU.

| Fase | Previsto dal campione | Misurato | Errore |
|---|---|---|---|
| s3, VM GPU (48 worker) | 91 s × 17.984 / 972 = 1.684 s | 2.109 s | +25% |
| s4, H100 | 281 s × 807.495 / 31.365 = 7.234 s | 9.833 s | +36% |
| s3, VM da 16 vCPU (16 worker), seconda tornata | 326 s × 18.001 / 972 = 6.037 s | 9.426 s | +56% |
| s4, H100, seconda tornata | 277 s × 807.916 / 31.365 = 7.135 s | 9.756 s | +37% |

Per s4 l'errore è lo stesso nelle due tornate. Per s3 invece sulla VM Haswell è più del doppio, quindi l'errore dipende anche dalla macchina e dal numero di worker (§7.6.3). La parte dell'errore comune alle due macchine invece dipende dai dati. Per capirlo ho confrontato i chunk dell'indice della baseline v0, che usa la stessa suddivisione in frammenti, separando quelli dei 500 farmaci del campione da quelli del resto del corpus.

| | Chunk | Lunghezza media | Chunk ≥ 1.400 caratteri | Chunk per documento |
|---|---|---|---|---|
| campione n500 (979 documenti) | 31.554 | 1.074 caratteri | 46% | 32,2 |
| resto del corpus (16.976 documenti) | 773.441 | 1.304 caratteri | 62% | 45,6 |

I primi 500 farmaci in ordine di codice sono i più vecchi, e i loro documenti sono più brevi e divisi in frammenti più corti. Un frammento lungo costa di più al modello, perché il calcolo dell'attenzione cresce con la lunghezza del testo. Inoltre il vantaggio del batch piccolo, che riduce il padding (§7.3), si riduce quando molti frammenti sono lunghi e quindi di lunghezza simile tra loro. Lo si vede dai numeri. Sul corpus completo il batch 64 elabora 82 chunk al secondo, un valore vicino ai 77 del batch 256 sul campione e molto lontano dai 112 dello stesso batch 64 sul campione.

Ho anche escluso che il rallentamento dipenda da Chroma, cioè che il database diventi più lento man mano che l'indice cresce. Per tutto il run ogni blocco di 256 chunk ha richiesto circa 3 secondi, dall'inizio alla fine.

Da questo ho tratto una conseguenza per il metodo. Le **proporzioni** tra le configurazioni misurate sul campione restano valide, perché tutte lavorano sugli stessi documenti, ma i **tempi assoluti** ricavati dal campione vanno corretti oppure presi dal full run. Per questo, dove possibile, calcolo il costo del corpus completo dai tempi misurati nel full run.

### 7.6 Tempo e costo

In questa sezione metto insieme i tempi misurati e i prezzi di §6 per stimare quanto costerebbe ogni configurazione su un cloud commerciale. Considero solo s3 e s4, cioè conversione e indicizzazione, perché s1 dura pochi secondi, s2 dipende dalla rete e dai server AIFA più che dalla macchina, e s5 la tratto a parte (§7.8).

Per ogni configurazione prendo la mediana delle ripetizioni dei run completi s3 + s4 e ci aggiungo l'overhead di creazione e preparazione della VM, ottenendo le ore pagate. Moltiplicando le ore per il prezzo orario di ogni fornitore ottengo il costo, di cui in tabella riporto il fornitore più economico e quello più caro. Il costo per 1.000 documenti serve a confrontare il campione con il corpus completo. Tutti i calcoli sono fatti da `bench/cost_model.py`, che salva i risultati in `results/costs.csv` e `results/costs_split.csv`.

Come per i tempi, riporto le due tornate nella stessa tabella, ognuna con i suoi tempi e il suo overhead (§6), mentre le osservazioni si basano sulla seconda. Prima presento le configurazioni con una sola macchina, poi quella eterogenea, che nella prima tornata avevo calcolato combinando i tempi delle singole fasi e nella seconda ho eseguito sul corpus completo.

#### 7.6.1 Configurazioni misurate

| Configurazione | Ore pagate, 1ª | Costo, 1ª | Ore pagate, 2ª | Costo, 2ª | € per 1.000 documenti, 2ª |
|---|---|---|---|---|---|
| `local-cpu`, campione (s3 + s4 su CPU) | 2,67 | 0,14 € | 2,34 | 0,12 € | 0,13 |
| `local-mps`, campione (s3 + s4 su MPS) | 7,08 | 0,37 € | 3,06 | 0,16 € | 0,16 |
| `cloud-cpu-s`, campione | 7,10 | 2,96 € (Azure) – 3,67 € (GCP) | 6,84 | 2,85 € (Azure) – 3,53 € (GCP) | 2,93 – 3,63 |
| `cloud-cpu-l`, campione | 4,15 | 3,46 € (Azure) – 4,21 € (GCP) | 4,24 | 3,53 € (Azure) – 4,30 € (GCP) | 3,64 – 4,42 |
| `cloud-gpu`, campione, batch 64 | 0,18 | 0,64 € (RunPod) – 1,79 € (Azure) | 0,16 | 0,56 € (RunPod) – 1,56 € (Azure) | 0,57 – 1,61 |
| `cloud-gpu-full`, corpus completo | 3,40 | 11,77 € (RunPod) – 33,10 € (Azure) | 3,38 | 11,69 € (RunPod) – 32,89 € (Azure) | 0,65 – 1,83 |

Il full run monolitico sul corpus completo l'ho eseguito solo nella prima tornata. Nella seconda uso gli stessi tempi con l'overhead nuovo, perché rifacendo s4 sul corpus completo il tempo è cambiato solo dello 0,8% (§7.6.3).

Osservazioni:
- Anche nella seconda tornata la VM con la H100 costa meno delle VM solo CPU, nonostante il prezzo orario molto più alto. Sulla CPU s4 dura ore, e il prezzo orario più basso non basta a compensarle.
- Su tutta la pipeline la VM da 16 vCPU costa ora il 24% in più di quella da 8, contro il 17% della prima tornata. Il prezzo orario è doppio, mentre con la stessa versione di PyTorch s4 accelera solo di 1,61 volte (§7.3). Una VM più grande conviene quindi solo per le fasi che scalano quasi linearmente come s3, ed è proprio quello che sfrutta la configurazione eterogenea (§7.6.2).
- Con la configurazione più veloce l'overhead pesa meno, ma resta molto più importante sui lavori brevi. Con la H100 su Azure aumenta del 57% il costo di un run sul campione (1,56 € invece di 0,99 €), contro il 78% della prima tornata, e solo dell'1,8% quello sul corpus completo (32,89 € invece di 32,32 €).
- Il campione sottostima anche i costi, perché i documenti reali sono più lunghi (§7.5). Senza contare l'overhead, su Azure 1.000 documenti costano 1,02 € sul campione e 1,80 € sul corpus completo.
- Il Mac con la GPU ora costa meno della metà (0,16 € invece di 0,37 €), perché s4 su MPS è diventata più veloce (§7.3), e con la CPU il campione costa 0,12 € in 2,3 ore. In euro il locale resta il più economico, ma si paga in tempo, e il corpus completo richiederebbe giorni di calcolo continuo (§7.7).

#### 7.6.2 Configurazione eterogenea

La configurazione eterogenea usa due VM una dopo l'altra. La prima è una VM solo CPU che esegue s3 con il numero di worker che la rende più veloce, la seconda è la VM con la H100 che esegue s4 con batch 64. Ognuna paga il proprio overhead di creazione e preparazione. Ho considerato solo Azure e AWS, perché tra i fornitori scelti sono gli unici che offrono sia VM solo CPU sia VM con una H100.

Nella prima tornata non l'avevo eseguita, ma l'avevo ricostruita a partire dai tempi delle singole fasi. Sul corpus completo non avevo misurato s3 sulla VM CPU, quindi l'avevo stimato moltiplicando il tempo sul campione per il rapporto tra corpus e campione misurato sulla VM GPU, cioè 2.109 / 91 = 23,2. Nella seconda tornata l'ho eseguita sul corpus completo con la VM da 16 vCPU (§7.6.3), e il costo di quelle righe viene dai tempi misurati, compreso il trasferimento dei Markdown tra le due VM. Le altre righe della seconda tornata sono ancora ricostruite. Per il campione uso i tempi delle singole fasi, mentre per la VM da 8 vCPU sul corpus completo stimo s3 con il rapporto tra corpus e campione misurato sulla stessa CPU Haswell, cioè 9.426 / 326 = 28,9.

| Corpus | Configurazione | Ore, 1ª | Azure, 1ª | AWS, 1ª | Ore, 2ª | Azure, 2ª | AWS, 2ª |
|---|---|---|---|---|---|---|---|
| campione | monolitica (VM GPU) | 0,18 | 1,79 € | 1,47 € | 0,16 | 1,56 € | 1,28 € |
| campione | split `cloud-cpu-s` (8 worker) + GPU | 0,41 | 1,65 € | 1,38 € | 0,36 | 1,41 € | 1,18 € |
| campione | split `cloud-cpu-l` (16 worker) + GPU | 0,33 | 1,69 € | 1,42 € | 0,28 | 1,44 € | 1,21 € |
| completo | monolitica (VM GPU) | 3,40 | 33,10 € | 27,16 € | 3,38 | 32,89 € | 26,99 € |
| completo | split `cloud-cpu-s` + GPU | 6,72 | 29,02 € | 24,21 € | 7,76 | 29,30 € | 24,55 € |
| completo | split `cloud-cpu-l` + GPU, eseguita nella 2ª | 4,95 | 29,17 € | 24,37 € | 5,49 | 29,48 € | 24,74 € |
| completo, con s2 | monolitica (VM GPU) | 6,06 | 59,05 € | 48,46 € | 6,04 | 58,84 € | 48,29 € |
| completo, con s2 | split: s2 e s3 su `cloud-cpu-l`, s4 su GPU, eseguita nella 2ª | 7,61 | 31,39 € | 26,73 € | 7,99 | 31,56 € | 26,94 € |

![Costo e tempo di s3 + s4 per configurazione e fornitore, seconda tornata](charts/costs_v2.png)

![Costo del corpus completo nelle due tornate](charts/rounds_costs.png)

Osservazioni:
- Sul corpus completo, contando solo s3 e s4, la configurazione eterogenea (*split* nella tabella) eseguita davvero costa il **10% in meno** della monolitica su Azure e l'8% in meno su AWS, ma impiega il 63% di tempo in più (5,49 h invece di 3,38 h). La stima della prima tornata prometteva un risparmio del 10–12% con il 46% di tempo in più. La differenza viene quasi tutta da s3, che sulla VM CPU è durata il 27% in più del previsto (§7.6.3). Il risparmio resta piccolo perché la parte più cara è s4, che in entrambi i casi gira sulla GPU.
- Le due VM CPU portano quasi allo stesso costo (29,30 € contro 29,48 € su Azure), ma quella da 16 vCPU finisce 2,3 ore prima, grazie allo scaling quasi lineare di s3 (§7.2). A parità di spesa conviene quindi la più grande.
- Il guadagno vero arriva togliendo il **download dalla GPU**. s2 occupa 2 h 40 min in cui né la GPU né la CPU lavorano (§7.4). Nella seconda tornata il download l'ho eseguito davvero sulla VM CPU, e il costo del corpus completo su Azure scende da 58,84 € a 31,56 € (−46%, e −44% su AWS), con circa 2 ore in più. Nella prima tornata queste righe le avevo calcolate a mano, ora le calcola `cost_model.py` con i tempi di s1 e s2 salvati in `full_s1s2.csv` dal full run e da `split-cpu`.
- Sul campione l'overhead pagato due volte riduce il vantaggio al 6–10%, quindi per i lavori brevi separare le fasi conviene poco.
- **Nel primo grafico** (seconda tornata) le configurazioni sono ordinate per costo. Sul campione la più economica è il Mac (0,12 €, ma 2,3 h) e la più veloce è la H100 (0,16 h, da 0,56 € con RunPod). Nessun'altra configurazione è più economica *e* più veloce di queste due, che per questo formano la *frontiera di Pareto*. Sul corpus completo invece la H100 di RunPod è sia la più veloce sia la più economica, con 11,69 € contro i 24,55–29,48 € della configurazione eterogenea su Azure e AWS. Questo mostra che la scelta del fornitore pesa più dell'architettura. La configurazione eterogenea conviene solo a parità di fornitore, e solo Azure e AWS offrono entrambi i tipi di VM. Lo stesso grafico per la prima tornata è in [charts/costs.png](charts/costs.png).
- **Il secondo grafico** confronta le due tornate sul corpus completo. La monolitica costa quasi uguale, mentre la split eseguita costa un po' più della stima, perché s3 sulla VM CPU è durata di più.

#### 7.6.3 La configurazione eterogenea eseguita

Nella seconda tornata ho eseguito davvero la configurazione eterogenea sul corpus completo, con due VM una dopo l'altra. Sulla VM da 16 vCPU (`split-cpu`) ho eseguito la creazione del database, il download e la conversione con 16 worker. Poi ho scaricato i Markdown e il database sul Mac con `make fetch-md` e li ho inviati alla VM con la H100 (`split-gpu`) con `make push-md` (§5.4), e lì ho eseguito s4 con batch 64. Il passaggio dal Mac non è obbligatorio, ma mi ha permesso di usare lo stesso Makefile senza dare a una VM l'accesso all'altra.

| Passo | Dove | Durata |
|---|---|---|
| s1 | VM da 16 vCPU | 6 s |
| s2, 25.198 PDF richiesti | VM da 16 vCPU | 8.973 s (2 h 30 min) |
| s3, 18.001 documenti con 16 worker | VM da 16 vCPU | 9.426 s (2 h 37 min) |
| Markdown e database dalla VM CPU al Mac (1,2 GB) | rete | 104 s |
| Markdown e database dal Mac alla VM GPU | rete | 97 s |
| s4, 807.916 chunk con batch 64 | H100 | 9.756 s (2 h 43 min) |

Osservazioni:
- s4 sulla H100 ha richiesto 9.756 s, contro i 9.833 s del full run della prima tornata (−0,8%) con quasi lo stesso numero di chunk. L'embedding del corpus completo è quindi riproducibile anche a tre giorni di distanza e con l'ambiente cambiato.
- s3 sulla VM CPU è durata il 27% in più dei ~7.400 s che avevo stimato in §7.6.2 con il rapporto tra corpus e campione della VM GPU (23,2). Sulla VM da 16 vCPU il rapporto misurato è 9.426 / 326 = 28,9. Una possibile spiegazione è che sulla VM GPU il run del campione con 48 worker sia allungato dalla coda finale sbilanciata (§7.2), che sul corpus completo pesa molto meno e quindi abbassa il rapporto. Non l'ho verificato, ma vuol dire che anche il rapporto tra corpus e campione dipende dalla macchina e dal numero di worker.
- Il trasferimento dei Markdown tra le due VM, che nel calcolo della prima tornata avevo ignorato, richiede in tutto circa 3 minuti e mezzo passando dal Mac, cioè l'1% del tempo di s3 + s4.
- Senza contare l'overhead, s3, trasferimento e s4 richiedono 19.383 s (5 h 23 min), contro gli 11.865 s della monolitica (2.109 s di s3 nel full run più 9.756 s di s4). Il tempo cresce quindi del 63%, mentre con la stima della prima tornata cresceva del 44%.
- Il download sulla VM CPU è durato il 6% in meno che sulla VM GPU nel full run (9.587 s), a conferma che dipende dalla rete e dai server AIFA più che dalla macchina.

### 7.7 Locale o cloud: punto di pareggio

In questa sezione rispondo alla domanda 4 di §1, cioè da quale dimensione del corpus conviene il cloud rispetto al Mac. Per ogni configurazione il tempo e il costo crescono in modo lineare con il numero di documenti *n*. C'è una parte fissa, che esiste solo sul cloud ed è l'overhead di creazione e preparazione della VM, più una parte che cresce con ogni documento, ricavata dai run completi s3 + s4 sul campione. In tabella ci sono entrambe le tornate, mentre i calcoli qui sotto usano la seconda.

| | Parte fissa | Tempo per documento | Costo per 1.000 documenti |
|---|---|---|---|
| Mac, CPU, 1ª tornata | — | 9,89 s | 0,14 € |
| Mac, CPU, 2ª tornata | — | 8,68 s | 0,13 € |
| VM H100, batch 64, 1ª tornata | 290 s; 0,28 € (RunPod) – 0,79 € (Azure) | 0,38 s | 0,37 € (RunPod) – 1,04 € (Azure) |
| VM H100, batch 64, 2ª tornata | 211 s; 0,20 € (RunPod) – 0,57 € (Azure) | 0,38 s | 0,36 € (RunPod) – 1,02 € (Azure) |

- **Tempo.** Il Mac impiega 8,68 secondi per documento, la H100 211 secondi fissi più 0,38 secondi per documento. I due tempi si eguagliano quando 8,68 *n* = 211 + 0,38 *n*, cioè con *n* ≈ 25. Sopra circa 25 documenti la H100 finisce quindi prima del Mac, anche contando l'overhead. Sul campione di 972 documenti impiega 0,16 h invece di 2,3 h. Nella prima tornata la soglia era di circa 30 documenti. Il Mac è diventato più veloce, ma l'overhead più corto ha spostato la soglia ancora più in basso.
- **Costo.** In euro il punto di pareggio non esiste. Anche senza contare la parte fissa, ogni documento sulla H100 costa da 2,9 (RunPod) a 8,1 volte (Azure) più che sul Mac, quindi il locale costa meno a ogni dimensione del corpus e non solo sotto una certa soglia. Questo risultato dipende però dall'ammortamento su 24 ore al giorno (§6), che è il caso più favorevole al locale. Se il Mac lavora meno ore, ogni ora deve ripagare una quota più grande del suo prezzo. Il Mac resta più economico della H100 di RunPod finché lavora in media almeno ~4,7 ore al giorno per i 4 anni di ammortamento, contro le ~5,4 della prima tornata. Sotto questa soglia l'ammortamento supera 0,122 €/h e la H100 di RunPod diventa più economica.
- **Corpus completo sul Mac (stima).** Non ho eseguito il corpus completo sul Mac, ma posso stimarne la durata scalando i tempi di s3 e s4 del campione con i rapporti tra corpus e campione misurati nella seconda tornata. Per s3 uso quello della VM da 16 vCPU (28,9), che ha un numero di worker vicino ai 10 del Mac, e per s4 quello della H100 (35,2, §7.5). Il risultato è di circa 82 ore, cioè **~3,4 giorni** di calcolo continuo, per circa 4,28 €. Sulla H100 servono invece 3,4 ore e da 11,69 a 32,89 €. Il cloud quindi non fa risparmiare denaro ma tempo, circa 79 ore in cambio di 7–29 € in più, cioè da 0,09 a 0,36 € per ogni ora risparmiata. Nella prima tornata la stima era di ~3,7 giorni e da 0,08 a 0,33 € per ora risparmiata.

### 7.8 s5: tempo di risposta a una domanda

Per verificare l'ipotesi H4 ho misurato s5 con `bench/s5_bench.py`, sull'indice del campione costruito da s4 nella seconda tornata. Lo script pone 20 domande fisse (`bench/s5_questions.txt`), ognuna su un farmaco diverso del campione, e ripete ogni domanda 3 volte. Per ogni domanda misura separatamente i tre passi della ricerca, cioè l'embedding della domanda, la ricerca dei 30 candidati in Chroma e il riordino con il reranker. La risposta del modello linguistico non la misuro, perché passa dalla rete e dall'API di Claude e non dipende dalla macchina. In tabella riporto la mediana delle 60 misure.

| Config | Device | Embedding (ms) | Ricerca (ms) | Reranker (ms) | Totale (ms) | 95° percentile (ms) |
|---|---|---|---|---|---|---|
| `local-cpu-v2` | CPU M4 | 44 | 2,6 | 9.660 | 9.709 | 13.841 |
| `local-mps-v2` | GPU M4 (MPS) | 38 | 9,8 | 4.291 | 4.335 | 5.937 |
| `cloud-gpu-v2` | H100 | 9,9 | 3,1 | 261 | 274 | 402 |
| `split-gpu` | H100, corpus completo | 9,9 | 3,6 | 283 | 296 | 474 |

Osservazioni:
- Quasi tutto il tempo va al reranker, che rilegge le 30 coppie domanda–frammento e occupa dal 95% al 99% del totale. L'embedding della domanda e la ricerca in Chroma insieme restano sotto i 50 ms su tutte le macchine.
- La H100 risponde in 0,27 s, 16 volte più veloce della GPU del Mac e 35 volte più veloce della sua CPU.
- Sul Mac per s5 conviene la GPU, 2,2 volte più veloce della CPU, mentre per s4 era il contrario (§7.3).
- Sul corpus completo l'indice ha 26 volte più frammenti, ma la ricerca in Chroma passa solo da 3,1 a 3,6 ms. Il totale cresce dell'8%, quasi tutto nel reranker, probabilmente perché i candidati sono frammenti più lunghi (§7.5).
- Nella baseline avevo osservato circa 4,5 s con MPS e 17,7 s su CPU (§3). Con MPS il tempo è lo stesso, mentre su CPU ora è circa la metà. Quella della baseline era una misura informale e con un altro ambiente, quindi non ho indagato la differenza.

Per il costo conta come si usa la GPU. Se la H100 rispondesse a domande senza sosta ne gestirebbe circa 13.000 all'ora, e una domanda costerebbe da 0,026 centesimi (RunPod) a 0,074 centesimi (Azure). Sul Mac con MPS le domande sono circa 830 all'ora, e con 0,0522 €/h (§6.1) una domanda costa 0,006 centesimi. Anche a pieno carico quindi una domanda costa di più sul cloud, da 4 a 12 volte a seconda del fornitore. Nell'uso reale le domande arrivano a intervalli, e una GPU tenuta accesa per rispondere costa da 3,46 a 9,74 € all'ora anche quando nessuno la usa, cioè da 83 a 234 € al giorno.

### 7.9 Un secondo modello di embedding

Il modello di embedding decide come vengono rappresentati i frammenti, e quindi quali candidati arrivano al reranker in s5. Durante la seconda tornata è uscito EmbeddingGemma 2 di Google [19], e ho voluto vedere quanto cambiano tempo, spazio e qualità della ricerca cambiando solo il modello di embedding. Ho tenuto fermo tutto il resto, cioè gli stessi Markdown, la stessa divisione in frammenti, lo stesso reranker e le stesse domande di s5.

```
                                         ┌─> bge-m3 ───────────> Chroma A ─┐
Markdown (s3) ──> frammenti (s4) ────────┤                                 ├─> s5: 30 candidati ──> reranker ──> 6 frammenti
                                         └─> EmbeddingGemma 2 ─> Chroma B ─┘
```

| | bge-m3 | EmbeddingGemma 2 |
|---|---|---|
| Parametri | 568 milioni | 744 milioni |
| Dimensione del vettore | 1.024 | 768 |
| Precisione con cui viene caricato | fp32 | bf16 |
| Prefisso aggiunto al testo | nessuno | `task: search result \| query: ` per la domanda, `title: none \| text: ` per il frammento |

Per poter cambiare modello ho reso il modello di embedding una variabile (`EMBED_MODEL` in `rag/config.py`), e s4 e s5 ora chiamano `encode_document` e `encode_query`, che aggiungono il prefisso giusto per ogni modello. EmbeddingGemma 2 richiede versioni di transformers e sentence-transformers più recenti di quelle fissate in `rag/constraints.txt`, quindi ho eseguito entrambi i modelli in un ambiente separato sulla VM con la H100, con la stessa versione di PyTorch.

Per la qualità, ogni domanda di s5 riguarda un farmaco preciso, e controllo due cose. La prima è se tra i 30 candidati c'è almeno un frammento di quel farmaco, la seconda è quanti dei 6 frammenti rimasti dopo il reranker sono di quel farmaco. Aggiungo una terza misura, che non dipende dalle domande. Rappresento ogni documento con la media dei vettori dei suoi frammenti, prendo i suoi 10 documenti più simili escludendo l'altro documento dello stesso farmaco, e conto quanti appartengono alla stessa classe ATC di primo livello (per esempio sistema nervoso o antimicrobici). Un valore più alto vuol dire che il modello mette vicini i farmaci della stessa area terapeutica.

| | bge-m3, campione | Gemma 2, campione | bge-m3, completo | Gemma 2, completo |
|---|---|---|---|---|
| s4 (s) | 277 | 134 | 9.756 | 4.540 |
| Indice Chroma | 407 MB | 376 MB | 11.763 MB | 10.972 MB |
| Farmaco giusto tra i 30 candidati | 19/20 | 20/20 | 17/20 | 19/20 |
| Frammenti del farmaco giusto tra i 6 finali | 94/120 | 110/120 | 77/120 | 96/120 |
| Vicini della stessa classe ATC | 29% | 37% | 64% | 74% |
| s5, embedding della domanda (ms) | 8,8 | 30,8 | 9,0 | 30,6 |
| s5, totale (ms) | 293 | 279 | 292 | 282 |

Per bge-m3 sul corpus completo uso l'indice di `split-gpu` (§7.6.3), costruito nell'ambiente principale. Sul campione i due ambienti danno con bge-m3 lo stesso tempo di s4 (277 s in entrambi).

Osservazioni:
- EmbeddingGemma 2 costruisce l'indice in meno della metà del tempo (2,1 volte più veloce sul corpus completo), anche se ha più parametri. Gran parte del vantaggio viene probabilmente dalla precisione, perché Gemma lavora in bf16 e bge-m3 in fp32, e sulla H100 i calcoli in bf16 sono molto più veloci. Il confronto misura quindi i due modelli come vengono caricati di default, non a parità di precisione (§9).
- I vettori di Gemma sono più piccoli del 25%, ma l'indice solo del 7%, perché Chroma salva anche il testo dei frammenti e i metadati.
- Gemma trova più spesso il farmaco giusto, e dopo il reranker restano più frammenti del farmaco giusto (96 contro 77 su 120 sul corpus completo). Il reranker è lo stesso, quindi la qualità finale dipende anche dai candidati che riceve dall'embedding.
- Con il corpus completo la qualità scende per entrambi i modelli, perché ci sono molti più farmaci simili tra cui confondersi, ma Gemma perde meno.
- Il tempo di risposta di s5 non cambia. L'embedding della domanda con Gemma richiede 31 ms invece di 9, ma il totale dipende quasi solo dal reranker (§7.8).
- 20 domande sono poche, e la misura dice solo se i frammenti sono del farmaco giusto, non se rispondono alla domanda. I risultati sulla qualità vanno quindi presi come un'indicazione.

Per vedere come i due modelli dispongono i documenti ho costruito una pagina interattiva, [`docs/embedding-space.html`](embedding-space.html), generata da `bench/emb_viz.py` con i vettori esportati da `bench/emb_export.py`. Ogni punto è un documento, la vista 3D usa la PCA e quella 2D t-SNE, e cliccando su un documento si vedono i suoi 10 vicini più simili con i due modelli. Le proiezioni conservano solo una parte della struttura, perché le prime tre componenti della PCA spiegano tra l'11% e il 14% della varianza. Per questo le misure in tabella le calcolo sui vettori originali e non sulle proiezioni.

Per questo progetto EmbeddingGemma 2 sarebbe la scelta migliore sia per il tempo sia per la qualità. Non ho sostituito bge-m3 nella pipeline perché richiede versioni delle librerie diverse da quelle con cui ho eseguito tutte le altre misure.

---

## 8. Discussione delle ipotesi

Prima di iniziare le misure, nel piano dell'estensione, avevo formulato quattro ipotesi su come si sarebbero comportate le diverse configurazioni. In questo capitolo le riprendo una per una e le confronto con i risultati del capitolo 7.

**H1. La configurazione eterogenea costa meno della monolitica, impiegando quasi lo stesso tempo.** L'idea era che separando le fasi la GPU venisse pagata solo durante l'embedding. L'ipotesi è **parzialmente confermata**. Lo spreco c'è davvero, perché nel full run la GPU è rimasta ferma per il 54% del tempo (§7.4). Nella seconda tornata ho eseguito davvero la configurazione eterogenea sul corpus completo. Separando s3 e s4 il costo scende dell'8–10%, contro il 10–12% della stima della prima tornata, e spostando sulla VM CPU anche il download il risparmio arriva al 44–46% (§7.6.2). Il tempo però non resta quasi uguale ma cresce del 63%, contro il 46% stimato, perché le VM solo CPU disponibili su Chameleon sono molto più lente della VM GPU nella conversione, e sul corpus completo lo sono ancora di più di quanto prevedeva la stima (§7.6.3).

**H2. s3 scala quasi linearmente fino a un certo numero di core, poi l'efficienza cala.** Mi aspettavo che oltre una certa soglia aggiungere vCPU servisse sempre meno, per via della lettura da disco e dell'avvio dei processi. Sul cloud l'ipotesi è **confermata**. Fino a 16 worker l'efficienza resta almeno a 0,87, poi scende a 0,73 con 32 e a 0,58 con 48 (§7.2), con valori quasi uguali nelle due tornate. In locale all'inizio **non era confermata**, perché sul Mac lo scaling si fermava a 2 worker. La causa però non era la macchina ma i thread interni di onnxruntime, insieme alla memoria occupata da altre applicazioni (§7.1.1). Dopo la correzione anche il Mac conferma l'ipotesi, con un'efficienza di 0,71 fino a 4 worker, cioè finché lavorano i core performance, che poi scende a 0,36 con 10.

**H3. Sotto una certa dimensione del corpus il locale vince, per l'overhead fisso del cloud.** L'idea era che per pochi documenti il tempo per creare e preparare la VM pesasse più del lavoro vero e proprio. Per il tempo l'ipotesi è **confermata**. Sotto circa 25 documenti il Mac finisce prima, per via dei circa 3,5 minuti di overhead (§5.5), mentre sopra vince la H100 (§7.7). Per il costo il risultato va oltre l'ipotesi, perché in euro il locale vince a ogni dimensione del corpus. Il motivo però non è l'overhead ma il costo per documento, che sul Mac è da 2,9 a 8,1 volte più basso con l'ammortamento su 24 ore al giorno. L'overhead pesa comunque molto sui lavori brevi, perché aumenta del 57% il costo di un run sul campione con la H100 e solo dell'1,8% quello sul corpus completo (§7.6).

**H4. Per l'interrogazione (s5) il cloud non conviene.** In locale una domanda richiede circa 4,5 secondi con la GPU del Mac, e gran parte del tempo va al reranker. Una GPU cloud la renderebbe più veloce, ma andrebbe tenuta sempre accesa per rispondere alle domande. Nella seconda tornata l'ho misurata, e l'ipotesi è **confermata per il costo ma non per il tempo**. La H100 risponde in 0,27 s, 16 volte più veloce della GPU del Mac (4,3 s), e il reranker occupa dal 95% al 99% del tempo su tutte le macchine (§7.8). In euro però una domanda costa da 4 a 12 volte di più sul cloud anche con la GPU sempre occupata, e con le domande che arrivano a intervalli si paga soprattutto la GPU ferma, da 83 a 234 € al giorno. Per un uso personale come il mio basta il Mac, mentre il cloud serve se la latenza conta o se le domande sono tante.

In sintesi:

| Ipotesi | Esito |
|---|---|
| H1, configurazione eterogenea più economica a tempo quasi uguale | parzialmente confermata, costa meno ma impiega di più |
| H2, s3 scala quasi linearmente e poi l'efficienza cala | confermata sul cloud e, dopo la correzione di s3, anche in locale |
| H3, sotto una certa dimensione del corpus vince il locale | confermata per il tempo (~25 documenti), in euro il locale vince sempre |
| H4, per s5 il cloud non conviene | confermata per il costo (da 4 a 12 volte più cara per domanda), non per il tempo (16 volte più veloce) |

---

## 9. Limiti e minacce alla validità

Nessuna misura è perfetta, e alcune scelte che ho fatto per contenere tempi e costi limitano quanto i risultati si possono generalizzare. In questo capitolo elenco i limiti che conosco e, dove possibile, quanto pesano.

- **Processori diversi.** Le VM `m1` usano CPU Intel Haswell, il nodo con la H100 un Xeon Platinum 8468 (Sapphire Rapids) e il Mac un Apple M4. Quando confronto s3 tra macchine diverse mescolo quindi il numero di core con la velocità di ogni core. Per questo in §7.2 riporto anche il tempo con un solo worker su ogni macchina, che misura la velocità del singolo core.
- **Core fisici o virtuali.** La virtualizzazione di Chameleon (KVM) mostra alla VM ogni vCPU come un processore separato, e non dice se dietro c'è un core fisico o un *hyperthread*, cioè metà di un core condiviso con un'altra vCPU. Oltre una certa soglia potrebbero essere hyperthread, e questo abbasserebbe l'efficienza misurata con 32 e 48 worker.
- **Versioni del software.** Nella prima tornata in locale usavo Python 3.13.4 e PyTorch 2.13.0, sul cloud Python 3.12.3 e PyTorch 2.14.1, e su `cloud-cpu-l` PyTorch 2.15.0, perché `bootstrap.sh` non fissava la versione di PyTorch e tra il 28 e il 29/10 ne è uscita una nuova. Per questo ho fissato PyTorch 2.14.1, l'ultima versione disponibile sia per macOS sia per Linux con build CPU e CUDA 12.6, ho allineato gli ambienti e ho ripetuto le misure (§4.4). Tra le due tornate i tempi cambiano al massimo del 3,3% per s3 e del 5% per s4, tranne che sulla GPU del Mac (§7.3). Le misure della seconda tornata però girano su VM nuove, quindi non posso separare l'effetto delle versioni da quello di un nodo fisico diverso.
- **OCR.** L'OCR cambia il testo estratto, non solo il tempo. Tra le serie con e senza OCR 142 documenti su 972 hanno un Markdown diverso, e i chunk passano da 31.905 a 31.365. Il Mac con OCR e il cloud producono invece lo stesso testo (33.719.565 contro 33.720.024 caratteri, con 233 pagine passate all'OCR in entrambi i casi). Le misure di s4 in locale usano i chunk senza OCR, ma la differenza è dell'1,7% e non cambia la velocità in modo apprezzabile.
- **Rappresentatività del campione.** Il campione sottostima del 25–36% i tempi del corpus completo, come ho mostrato in §7.5.
- **Misure singole.** Alcune misure le ho eseguite una sola volta perché duravano ore. Sono s4 su CPU e su MPS, il full run, i batch 32 e 128, e s3 con un solo worker sulla VM GPU e su `cloud-cpu-l`.
- **Rete.** La durata di s2 nel full run dipende dal server AIFA e dal collegamento transatlantico fino al Texas, quindi non è una misura della VM.
- **Baseline.** La durata di s4 nella baseline (~2,5 h) l'avevo osservata e non misurata. Il confronto con il full run (2 h 44 min, con batch e software diversi) è quindi solo indicativo.
- **Potenza del Mac.** Per il costo dell'energia uso il consumo massimo dichiarato da Apple, non un consumo misurato.
- **Prezzi.** Uso i listini commerciali come equivalenti perché Chameleon è gratuito, ma le risorse non sono identiche (virtualizzazione, rete, disco, modello di H100). I prezzi inoltre cambiano nel tempo, per questo ognuno ha la sua data e la sua fonte.
- **Configurazione eterogenea.** Nella prima tornata l'avevo solo calcolata, ignorando il trasferimento dei Markdown e stimando s3 sul corpus completo con il rapporto tra corpus e campione della VM GPU. Nella seconda l'ho eseguita (§7.6.3), ma i Markdown passano dal Mac, quindi il tempo di trasferimento dipende anche dalla mia connessione. Un trasferimento diretto tra le due VM sarebbe probabilmente più veloce.
- **s5 senza modello linguistico.** Il tempo di s5 non comprende la risposta del modello linguistico, che dipende dalla rete e dall'API e non dalla macchina.
- **Modelli di embedding.** Ho confrontato bge-m3 e EmbeddingGemma 2 in un ambiente separato sulla VM GPU, con transformers e sentence-transformers più recenti e con il codice di s4 e s5 modificato ma non ancora nel commit (in `phases.csv` il commit risulta `fc80997-dirty`). bge-m3 lavora in fp32 e EmbeddingGemma 2 in bf16, quindi il vantaggio di tempo dipende anche dalla precisione. La qualità è misurata su sole 20 domande e controlla solo il farmaco dei frammenti (§7.9).
- **Overhead.** L'ho misurato su una VM CPU di Chameleon e l'ho assunto uguale per la VM GPU. Sui cloud commerciali la creazione di una VM può essere più rapida o più lenta.
- **Log.** PyMuPDF stampa prima di ogni messaggio un blocco crescente di byte nulli, che portava i log fino a 19 MB per run. Il problema riguarda solo la dimensione dei log e non le misure, e ora `run.sh` filtra quei byte.

---

## 10. Conclusioni

Riprendo le cinque domande di §1 e rispondo in base ai risultati.

1. **Quanto costa rendere l'infrastruttura riproducibile?** Poco. Con circa 170 righe tra Makefile e Terraform la VM è raggiungibile in circa 70 secondi invece dei 7 minuti della procedura manuale, ed è pronta per le misure in circa 3 minuti e mezzo, contro i 5 della prima tornata, prima di installare l'ambiente con uv. La distruzione libera tutto in circa 20 secondi, senza dimenticare l'IP pubblico. Il tempo che resta è quasi tutto nella configurazione del software (circa 145 s), che un'immagine con l'ambiente già installato ridurrebbe. Sul corpus completo questo overhead vale l'1,8% del costo.
2. **Qual è la configurazione migliore per ciascuna fase?** Il download (s2) dipende dalla rete e va eseguito sulla macchina più economica. La conversione (s3) vuole molti core e scala quasi linearmente fino a 16 worker. Il Mac, dopo la correzione di s3, la esegue in locale quanto una VM da 16 vCPU. L'embedding (s4) vuole la GPU, che è da 29 a 86 volte più veloce delle CPU che ho misurato. L'interrogazione (s5) per un uso personale resta in locale, perché la H100 risponde 16 volte più veloce del Mac ma ogni domanda costa da 4 a 12 volte di più. Il modello di embedding conta quanto la macchina, perché con EmbeddingGemma 2 s4 è 2,1 volte più veloce e la ricerca trova più spesso il farmaco giusto.
3. **Una VM unica è uno spreco?** Sì, perché la H100 resta ferma per il 54% del full run. Separare solo s3 fa risparmiare poco, l'8–10% misurato eseguendo davvero la configurazione eterogenea, perché la parte più cara è s4. Separare anche il download fa risparmiare quasi metà del costo (44–46%), al prezzo di circa due ore in più.
4. **Quando conviene il cloud rispetto alla macchina locale?** In tempo il cloud vince già sopra circa 25 documenti. In euro vince sempre il locale, ma al Mac il corpus completo richiederebbe circa 3,4 giorni. Il cloud quindi vende tempo, a 0,09–0,36 € per ogni ora risparmiata. Per la stessa GPU i fornitori differiscono di quasi 3 volte, e questa scelta pesa più di quella dell'architettura.
5. **Come scala la conversione da PDF a Markdown con il numero di core?** Sul cloud quasi linearmente fino a 16 worker (efficienza 0,87–0,90), poi sempre meno (0,58 con 48). Sul Mac all'inizio si fermava a 2 worker, per i thread interni di onnxruntime e la memoria occupata da altre applicazioni. Dopo la correzione scala bene fino a 4 worker e poi meno, perché i core efficiency sono più lenti, e con 10 worker converte il campione in 308 s, quanto la VM da 16 vCPU.

Dal lavoro ho tratto anche una lezione di metodo. Il campione fisso rende i confronti ripetibili ed economici, ma i primi 500 farmaci non rappresentano bene il corpus e sottostimano i tempi reali del 25–36%. Scelte come la dimensione del batch, che sul campione sembravano chiare, vanno quindi verificate sul carico reale. Anche ripetere le misure è servito. Con gli ambienti allineati i tempi delle VM sono cambiati di pochi punti percentuali, mentre il risultato del Mac è cambiato del tutto, e la configurazione eterogenea eseguita davvero è risultata più lenta di quanto prevedeva la stima.

Il lavoro si può estendere in diverse direzioni:
- creare un'immagine con l'ambiente già installato, per ridurre la configurazione della VM;
- trasferire i Markdown direttamente tra le due VM della configurazione eterogenea, senza passare dal Mac;
- confrontare i modelli di embedding a parità di precisione e con più domande, giudicando a mano se i frammenti rispondono;
- portare EmbeddingGemma 2 nella pipeline principale, aggiornando le librerie su tutte le macchine.

---

## Appendice A — Riproduzione delle misure locali

```bash
python -m venv .venv && .venv/bin/pip install -r rag/requirements.txt
bench/make_sample.sh 500                                       # campione fisso
for w in 1 2 4 8 10; do for r in 1 2 3; do
  CONFIG=local-cpu-ocr WORKERS=$w bench/run.sh samples/n500 s3 # scaling s3 (Tesseract installato)
done; done
CONFIG=local-cpu DEVICE=cpu bench/run.sh samples/n500          # s3 + s4 su CPU
CONFIG=local-mps DEVICE=mps bench/run.sh samples/n500          # s3 + s4 su MPS
CONFIG=local-mps DEVICE=mps .venv/bin/python bench/s5_bench.py results/local-mps/<run_id>/data  # s5
.venv/bin/python bench/collect.py                              # -> results/all.csv
.venv/bin/python bench/cost_model.py                           # -> results/costs*.csv
.venv/bin/python bench/charts.py                               # -> docs/charts/*.png
```

## Appendice B — Riproduzione delle misure cloud

Prerequisiti: credenziali applicative di Chameleon in `~/.config/openstack/clouds.yaml` (cloud
`chameleon`), Terraform, la CLI `openstack` con il plugin Blazar, `jq`, una coppia di chiavi registrata
su Chameleon.

```bash
make lease up deploy bench                     # cloud-cpu-l: piano in bench/plan_cloud-cpu-l.sh
make fetch down                                # a fine misure (log sulla VM: ~/bench.log, BENCH_DONE)
make lease up deploy bench FLAVOR_ID=<id> LEASE_NAME=<nome> CONFIG=<config>   # altre configurazioni
.venv/bin/python bench/collect.py
```

---

## Riferimenti

1. AIFA - Agenzia Italiana del Farmaco, *Liste dei farmaci* (open data).
   https://www.aifa.gov.it/it/liste-dei-farmaci (consultato il 09/07/2026).
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
16. OpenStack, *OpenStackClient*. https://docs.openstack.org/python-openstackclient/latest/
17. Astral, *uv*. https://docs.astral.sh/uv/
18. *ONNX Runtime, Thread management*. https://onnxruntime.ai/docs/performance/tune-performance/threading.html
19. Google, *EmbeddingGemma 2*. https://huggingface.co/google/embeddinggemma-2
