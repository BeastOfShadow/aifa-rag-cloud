# Lavoro svolto — RAG sui documenti AIFA con elaborazione su cloud

Questo documento ricostruisce, passo per passo, tutto il lavoro fatto finora sul progetto: dai dati di
partenza fino al sistema RAG interrogabile in locale, con particolare attenzione al ruolo del cloud
(Chameleon Cloud). Le note tecniche estese e i grafici sono in [docs/ARTICOLO.md](docs/ARTICOLO.md).

---

## 0. In sintesi

| Cosa | Dove | Numeri |
|---|---|---|
| Dati anagrafici AIFA (3 CSV) → SQLite | locale / cloud | 12.599 farmaci unici |
| Download Foglietti Illustrativi (FI) e Riassunti delle Caratteristiche del Prodotto (RCP) | cloud | 25.198 PDF tentati, ~17.959 disponibili |
| Conversione PDF → Markdown (con OCR) | cloud, 48 vCPU | 37 min (vs ~15 h stimate in seriale) |
| Chunking + embedding (bge-m3) → ChromaDB | cloud, GPU H100 | ~805.000 chunk in ~2,5 h (vs ~50 h stimate su CPU locale) |
| Retrieval a 2 stadi + generazione LLM | locale (Mac mini M4) | ~4,5 s/query con GPU Apple (MPS) |

Idea di fondo: **ogni fase gira sull'hardware adatto**. Il cloud è stato usato come risorsa di calcolo
per le fasi pesanti e una tantum (conversione e indicizzazione); l'interrogazione, leggera e
interattiva, resta in locale.

---

## 1. Obiettivo

Costruire un sistema di **Retrieval-Augmented Generation (RAG)** sui documenti ufficiali dei medicinali
italiani pubblicati da AIFA (FI e RCP), collegati ai dati anagrafici strutturati (codice ATC, principi
attivi, confezioni, ditta titolare).

Vincoli scelti:
- dati, embedding e database vettoriale **tutti in locale/proprietari**, senza servizi gestiti esterni;
- solo la **generazione** della risposta finale passa da un LLM via API, dietro un'interfaccia
  intercambiabile (Claude API oppure Ollama in locale);
- priorità alla **precisione** delle risposte, il tempo di elaborazione è secondario.

---

## 2. Dati di partenza

Tre file CSV open data AIFA, in [datasets/](datasets/):

| File | Righe | Contenuto |
|---|---|---|
| `atc.csv` | 7.209 | Classificazione ATC → descrizione |
| `PA_confezioni.csv` | 337.550 | Principi attivi per confezione (codice AIC) |
| `confezioni_fornitura.csv` | 159.708 | Anagrafica confezioni + link ai PDF di FI e RCP |

Osservazione che ha ridotto il lavoro del 92%: le 159.708 confezioni corrispondono a soli **12.599
farmaci** distinti (`COD_FARMACO`), e i PDF sono per farmaco, non per confezione. Deduplicando si passa
da ~320.000 link a **25.198 PDF** (un FI e un RCP per farmaco).

---

## 3. Scelta architetturale: RAG ibrido

Non tutto deve finire negli embedding. Le domande sui dati tabellari ("che ATC ha il farmaco X?",
"quali confezioni ha la ditta Y?") hanno una risposta esatta e vanno risolte in **SQL**: deterministico,
immediato, senza allucinazioni. Il RAG serve solo per il **testo libero** dei documenti.

Il sistema ha quindi due livelli:
1. **Strutturato** — SQLite con indici sulle chiavi di join.
2. **Vettoriale** — ChromaDB con i frammenti (chunk) dei documenti trasformati in vettori.

Il collegamento tra i due sono i metadati di ogni chunk (`cod_farmaco`, `tipo` FI/RCP, `section`,
`denominazione`, `codice_atc`, `ragione_sociale`), che permettono di filtrare la ricerca semantica con
criteri strutturati.

---

## 4. La pipeline, passo per passo

Gli script sono in [rag/](rag/), la configurazione centrale in [rag/config.py](rag/config.py).
I passi 2 e 3 sono **riprendibili**: se interrotti, al rilancio saltano ciò che è già stato fatto.
Il passo 1 ricrea le tabelle anagrafiche (veloce), il passo 4 ricostruisce l'indice da zero.

### Passo 1 — Dati strutturati → SQLite ([s1_structured.py](rag/s1_structured.py))
- Lettura dei tre CSV forzando tutte le colonne a stringa (i codici AIC hanno zeri iniziali:
  `000152037` letto come numero diventerebbe `152037` e i join fallirebbero).
- Scrittura in `data/farmaci.sqlite` (tabelle `atc`, `pa`, `conf`) con indici sulle chiavi.

### Passo 2 — Download dei PDF ([s2_download.py](rag/s2_download.py))
- Download asincrono (`httpx` + `asyncio`) con concorrenza limitata a 6 richieste, per non
  sovraccaricare i server AIFA, retry con backoff e user-agent identificativo.
- Cache: i file già scaricati (o già convertiti) vengono saltati.
- Ogni esito (ok, 404, 500, byte scaricati) è registrato in una tabella `docs` di SQLite che fa da
  **manifest** del corpus.

### Passo 3 — PDF → Markdown ([s3_pdf_to_md.py](rag/s3_pdf_to_md.py))
- Conversione con `pymupdf4llm`, che mantiene la struttura (titoli, elenchi, tabelle) in Markdown.
- Le pagine-immagine e i documenti scansionati vengono passati in OCR (Tesseract, lingua italiana).
- **Parallelizzazione** con `multiprocessing.Pool` su tutti i core disponibili: in seriale l'OCR
  portava la stima a ~15 ore, sui 48 vCPU della VM il lavoro è durato **37 minuti**.
- I documenti con poco testo per pagina vengono marcati come scansionati per il report finale.
- Il PDF viene cancellato dopo la conversione (resta solo il Markdown, ~1 GB in totale).

### Passo 4 — Chunking, embedding e indicizzazione ([s4_index.py](rag/s4_index.py))
- **Pulizia**: rimozione del testo legale AIFA ripetuto in ogni documento ("Documento reso disponibile
  da AIFA il…"), che altrimenti inquinerebbe la ricerca.
- **Chunking per sezione**: FI e RCP hanno sezioni numerate standard (es. RCP 4.3 Controindicazioni,
  4.8 Effetti indesiderati; FI 1–5). Lo splitter riconosce i marcatori numerati e taglia lì; le sezioni
  troppo lunghe vengono spezzate a 1.800 caratteri con 150 di sovrapposizione.
- **Prefisso di contesto**: ogni chunk viene trasformato in vettore come
  `{denominazione} — {tipo} — {sezione}: {testo}`, così il vettore "sa" di quale farmaco e di quale
  sezione si tratta; nel database resta salvato il testo originale.
- **Embedding** con il modello `BAAI/bge-m3` (multilingua, adatto all'italiano), su GPU,
  a batch da 256.
- Salvataggio in ChromaDB persistente (`data/chroma`, ~11 GB): **804.995 chunk**.

### Passo 5 — Report dei mancanti ([report_missing.py](rag/report_missing.py))
- Dal manifest produce `data/missing.csv`: ogni FI/RCP non indicizzato con motivo (404, 500, errore),
  farmaco, ATC e ditta. Esito: ~7.000 documenti mai pubblicati da AIFA (404, attesi) e 58 errori 500
  transitori, recuperabili rilanciando.

### Passo 6 — Interrogazione ([s5_query.py](rag/s5_query.py), [ui.py](rag/ui.py))
- **Stadio 1, recall**: la domanda viene trasformata in vettore con bge-m3 e si recuperano i 30 chunk
  più simili (con filtri opzionali sui metadati).
- **Stadio 2, precisione**: un cross-encoder (`BAAI/bge-reranker-v2-m3`) rivaluta le 30 coppie
  domanda–chunk e tiene le 6 migliori.
- **Generazione**: i 6 chunk e la domanda vanno all'LLM scelto (Claude o Ollama), che risponde citando
  le fonti.
- Interfaccia Streamlit per esplorare sia la ricerca semantica sia le query SQL.

---

## 5. Il ruolo del cloud: Chameleon Cloud

### 5.1 Perché
Sul Mac mini M4 locale due fasi non erano praticabili:
- la conversione con OCR (~15 h stimate su un core);
- l'embedding di ~805.000 chunk (~50 h stimate su CPU; la GPU integrata del M4, via MPS, a batch grandi
  è risultata addirittura **più lenta** della CPU).

### 5.2 Risorse usate
- Sito **KVM@TACC** di Chameleon Cloud (piattaforma OpenStack per la ricerca).
- Risorsa ottenuta tramite **lease** (prenotazione a tempo): VM con **1 GPU NVIDIA H100 (94 GB)**,
  **48 vCPU**, **250 GB di RAM**, Ubuntu 22.04.

### 5.3 Come
1. **Prenotazione** del lease e avvio dell'istanza (fatto manualmente dalla dashboard).
2. **Bootstrap riproducibile** con [chameleon/bootstrap.sh](chameleon/bootstrap.sh): verifica driver
   NVIDIA, installazione dipendenze di sistema (Python, OCR Tesseract in italiano), creazione del
   virtualenv, installazione di PyTorch con la build CUDA corretta, verifica che la GPU sia visibile.
3. **Run completo** con [chameleon/run_full.sh](chameleon/run_full.sh) (passi 1→5), lanciato in modo
   da sopravvivere alla chiusura della sessione SSH.
4. **Ripresa** dopo interruzione con [chameleon/run_rest.sh](chameleon/run_rest.sh), che riparte dalla
   conversione sfruttando il fatto che ogni passo salta il lavoro già fatto.
5. **Rientro dei risultati** in locale via `rsync` (`data/chroma`, `data/md`, `data/farmaci.sqlite`,
   `data/missing.csv`), poi rilascio della risorsa.

### 5.4 Risultati locale vs cloud

| Fase | Locale (Mac mini M4) | Cloud (Chameleon, H100 + 48 vCPU) | Guadagno |
|---|---|---|---|
| PDF → Markdown, ~18.000 doc | ~15 h stimate (seriale) | **37 min** (parallelo) | ~24× |
| Embedding, ~805.000 chunk | ~50 h stimate (CPU) | **~2,5 h** (GPU) | ~20× |
| Embedding, singolo batch da 256 | CPU 68 s · MPS 106 s | 4,6 s | ~15× vs CPU |

Per l'interrogazione, invece, il locale basta: con la GPU Apple (MPS) la latenza mediana è di ~4,5 s per
domanda contro ~17,7 s su sola CPU. Il collo di bottiglia è il reranker (oltre il 90% del tempo); la
ricerca vettoriale in ChromaDB su 805.000 chunk richiede meno di 15 ms.

### 5.5 Problemi incontrati sul cloud
- **Versione CUDA**: `pip install torch` installava la build per CUDA 13, mentre il driver della VM
  supportava CUDA 12.6 → GPU non vista. Risolto installando esplicitamente la build `cu124`.
- **OCR automatico non voluto**: con Tesseract installato, MuPDF fa OCR su *ogni* pagina-immagine
  (loghi, filigrane), passando da 0,07 s a 6,9 s per documento. Invece di disattivarlo (i flag non
  funzionavano) l'ho tenuto, perché il testo delle immagini è utile al RAG, e ho parallelizzato.
- **Sessione SSH**: il run, lanciato con `setsid`, prosegue anche se la connessione cade; un errore 255
  della SSH non indica che il processo sia morto (verificato sull'albero dei processi).

---

## 6. Limiti dello stato attuale

Il professore ha osservato che il cloud non era abbastanza evidente nel lavoro. In effetti, finora:
- il provisioning (lease, istanza, rete, chiavi) è stato fatto **a mano** dalla dashboard;
- è stata misurata **una sola configurazione cloud**, quindi manca un vero confronto;
- non c'è una **stima dei costi**;
- una parte della VM è rimasta inutilizzata: durante i 37 minuti di conversione (solo CPU) la H100 era
  ferma, e durante l'embedding i 48 vCPU erano quasi inattivi;
- alcuni numeri sono stime o misure non ripetute (ad esempio, 55 chunk/s misurati su un batch darebbero
  ~4 h per 805.000 chunk, non le ~2,5 h osservate nel run completo): vanno rimisurati in condizioni
  controllate.

Questi limiti sono il punto di partenza dell'estensione concordata con il professore (opzione 3):
**infrastructure as code e confronto di costi e prestazioni tra configurazioni diverse**.
Il piano è in [APPROFONDIMENTO_IAC.md](APPROFONDIMENTO_IAC.md).
