# Una conversione fedele dei documenti AIFA, da PDF a Markdown strutturato

**Simone Negro** — matricola 20051767 — Università del Piemonte Orientale
Lavoro successivo al progetto per il corso del prof. Massimo Canonico — in corso, 04/11/2026

---

## 1. Obiettivo

La pipeline del progetto (docs/RELAZIONE.md) converte i PDF in Markdown con pymupdf4llm. Per misurare tempi e costi bastava, ma guardando i Markdown da vicino ho visto che il testo c'è quasi tutto mentre la struttura no. I titoli delle sezioni finiscono in mezzo al testo, le tabelle che attraversano due pagine si spezzano e la filigrana di AIFA finisce dentro le tabelle.

Con questo lavoro voglio una conversione fedele, senza badare al tempo né al costo, perché posso usare le GPU H100 di Chameleon. Da lì voglio:
- dividere ogni documento nelle sue sezioni standard;
- costruire un dizionario di tutto il corpus, per capire quali parole e soprattutto quali frasi ripetute togliere e quali termini tenere;
- costruire una mappa generale dei documenti, delle sezioni e di quello che contengono.

Il lavoro sta tutto nella cartella `conv/` e non tocca la pipeline del progetto, che resta quella dell'esame. I Markdown nuovi sostituiranno quelli attuali solo alla fine, dopo aver misurato con s5 se la ricerca migliora.

## 2. Come sono fatti FI e RCP

Per ogni farmaco AIFA pubblica due documenti, il foglio illustrativo (FI) per il paziente e il riassunto delle caratteristiche del prodotto (RCP) per il medico. Il codice ATC invece non è un documento ma la classificazione del farmaco, che abbiamo già nei metadati.

Entrambi i documenti seguono il template europeo QRD dell'EMA (*Quality Review of Documents*), che AIFA richiede anche per i farmaci autorizzati solo in Italia. Le sezioni quindi sono fisse.

| RCP | Sezioni |
|---|---|
| 1, 2, 3 | denominazione, composizione qualitativa e quantitativa, forma farmaceutica |
| 4 | informazioni cliniche: 4.1 indicazioni, 4.2 posologia, 4.3 controindicazioni, 4.4 avvertenze, 4.5 interazioni, 4.6 fertilità, gravidanza e allattamento, 4.7 guida di veicoli, 4.8 effetti indesiderati, 4.9 sovradosaggio |
| 5 | proprietà farmacologiche: 5.1 farmacodinamiche, 5.2 farmacocinetiche, 5.3 dati preclinici |
| 6 | informazioni farmaceutiche: da 6.1 eccipienti a 6.6 smaltimento |
| 7, 8, 9, 10 | titolare, numero AIC, data della prima autorizzazione, data di revisione del testo |
| 11, 12 | solo per i radiofarmaci: dosimetria e preparazione |

| FI | Sezioni |
|---|---|
| 1 | cos'è e a cosa serve |
| 2 | cosa deve sapere prima di prenderlo, con sottosezioni fisse (non prenda, avvertenze e precauzioni, altri medicinali, gravidanza e allattamento, guida di veicoli) |
| 3 | come prenderlo |
| 4 | possibili effetti indesiderati |
| 5 | come conservarlo |
| 6 | contenuto della confezione e altre informazioni |

Nella 4.8 dell'RCP gli effetti indesiderati sono di solito in una tabella, con le classi di organi sulle righe e le frequenze (molto comune, comune, non comune, raro, molto raro, non nota) sulle colonne.

## 3. Quanto il corpus segue lo standard

Ho cercato i titoli standard nei 17.962 Markdown attuali, 8.981 FI e 8.981 RCP:

| | Seguono lo standard | Formato diverso |
|---|---|---|
| RCP | il 95% ha almeno 15 delle 18 sottosezioni da 4.1 a 6.6, il 65% le ha tutte | l'1,1% ne ha meno di 8 |
| FI | il 94% ha almeno 5 delle 6 sezioni | il 3,4%, probabilmente formati più vecchi del template |

Lo standard quindi c'è, e dividere i documenti in sezioni è fattibile per quasi tutto il corpus. Il 2–4% che non lo segue va riconosciuto e controllato a parte.

Altre due cose che ho visto:
- nessun PDF è una scansione, tutti hanno il testo nativo. Il converter quindi non deve leggere il testo ma ricostruire la struttura;
- in 4.258 RCP su 8.981 (47%) la filigrana "Documento reso disponibile da AIFA" finisce dentro una tabella, cioè la tabella è spezzata tra due pagine.

## 4. Cosa non va nella conversione attuale

Un esempio è la tabella degli effetti indesiderati di VIRSIL (RCP 047058). Nel Markdown l'intestazione è finita tutta in una cella, la tabella è interrotta dal piè di pagina legale di AIFA e restano frammenti come "nti edema" e lettere isolate che vengono dalle note in apice.

Ho misurato la conversione attuale sul campione di prova (§6) con le metriche di §7:

| Misura | s3 attuale |
|---|---|
| parole del PDF ritrovate nel Markdown (recall) | 0,97 |
| parole del Markdown presenti nel PDF (precision) | 0,79 |
| titoli standard riconosciuti come titoli | 7,6% |
| tabelle con dentro la filigrana | 174 su 226 |
| righe di tabella con un numero di celle diverso dall'intestazione | 16% |

Il testo quindi c'è quasi tutto, ma solo un titolo su tredici è riconoscibile come titolo. La precision sotto 1 viene in parte dal contenuto delle tabelle che in alcuni RCP compare due volte.

## 5. Piano

| Fase | Cosa | Risultato |
|---|---|---|
| 1 | riscaricare tutti i PDF disponibili su AIFA e questa volta tenerli | `conv/data/pdf`, circa 6,6 GB |
| 2 | scegliere un campione di prova di 40 documenti | `conv/gold.csv` |
| 3 | confrontare i converter sul campione con la H100 | scelta del converter, con i numeri |
| 4 | convertire tutto con il converter scelto, sulle 4 H100 se disponibili | Markdown e struttura JSON di ogni documento, con un controllo di qualità per documento |
| 5 | dividere in sezioni standard | una riga per sezione, con il suo identificativo (RCP 4.8, FI 2…) |
| 6 | dizionario di parole, coppie e terne di parole e frasi ricorrenti, per sezione | elenco delle frasi da togliere e dei termini da proteggere |
| 7 | mappa generale di documenti, sezioni, principi attivi, ATC ed effetti indesiderati | grafo navigabile |
| 8 | sostituire i Markdown del RAG e rifare s4 dividendo per sezioni | misura con s5 del miglioramento della ricerca |

## 6. Campione di prova

Ho scelto 40 documenti per gruppi, uno per farmaco e con un seme fisso (`conv/gold.py`). Ogni gruppo copre un caso che la conversione attuale sbaglia o che un converter può sbagliare.

| Gruppo | Documenti | Candidati |
|---|---|---|
| RCP standard | 6 | 2.442 |
| FI standard | 6 | 4.413 |
| RCP formato vecchio | 4 | 111 |
| FI formato vecchio | 4 | 308 |
| RCP con tabella spezzata | 6 | 4.258 |
| FI con tabelle | 3 | 1.081 |
| RCP molto lungo (5% più lungo) | 3 | 449 |
| FI molto lungo | 2 | 446 |
| RCP di radiofarmaco | 2 | 61 |
| RCP e FI qualsiasi | 4 | tutti |

In tutto sono 980 pagine, da 3 a 216 per documento, con una mediana di 11.

## 7. Come misuro la qualità

Non ho un riferimento scritto a mano, quindi confronto ogni Markdown con il testo nativo del suo PDF (`conv/score.py`):
- **fedeltà del testo**: quante parole del PDF ritrovo nel Markdown (recall, se è bassa il converter perde testo) e quante parole del Markdown ci sono nel PDF (precision, se è bassa il converter inventa o ripete). Filigrana e piè di pagina legale li tolgo da entrambi con la stessa regola;
- **sezioni**: quanti titoli standard presenti nel PDF sono un titolo anche nel Markdown, cioè a inizio riga come intestazione o in grassetto;
- **tabelle**: quante sono, quante righe hanno un numero di celle diverso dall'intestazione e quante contengono la filigrana;
- **tempo** per pagina sulla H100.

La precision è importante soprattutto per i converter che leggono le pagine come immagini, perché ogni tanto inventano o saltano parole, e su un documento medico è il rischio peggiore. Oltre ai numeri controllerò a occhio i documenti con il PDF e il Markdown affiancati.

## 8. Converter confrontati

| Converter | Versione | Come funziona |
|---|---|---|
| s3 attuale (pymupdf4llm) | quella del progetto | testo del PDF, tabelle ricostruite da MuPDF |
| Docling | 2.141.0 | testo del PDF più un modello di layout e TableFormer per le tabelle |
| Docling con GraniteDocling | 2.141.0 | un modello visivo (VLM) piccolo che legge le pagine come immagini |
| marker | 2.0.0 | testo del PDF più il VLM surya per layout e tabelle |
| MinerU | 4.0.12, livello advanced | VLM, con server locale e telemetria spenta |
| olmOCR | 0.4.27 | VLM (Qwen2.5-VL addestrato per l'OCR) che riceve l'immagine della pagina e il testo del PDF |

Ho fissato per ognuno l'ultima versione uscita da almeno due settimane. Ogni converter ha il suo ambiente Python, perché le dipendenze sono incompatibili tra loro (`conv/plan_bakeoff.sh`).

## 9. Risultati

Da completare dopo il confronto sulla H100.
