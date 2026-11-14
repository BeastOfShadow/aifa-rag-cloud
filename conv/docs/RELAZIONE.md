# Una conversione fedele dei documenti AIFA, da PDF a Markdown strutturato

**Simone Negro** — matricola 20051767 — Università del Piemonte Orientale
Lavoro successivo al progetto per il corso del prof. Massimo Canonico — in corso, 14/11/2026

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
| parole del PDF ritrovate nel Markdown (recall) | 0,99 |
| parole del Markdown presenti nel PDF (precision) | 0,77 |
| titoli standard riconosciuti come titoli | 21% |
| tabelle con dentro la filigrana | 174 su 226 |
| righe di tabella con un numero di celle diverso dall'intestazione | 16% |

Il testo quindi c'è quasi tutto, ma solo un titolo su cinque è riconoscibile come titolo. La precision sotto 1 viene in parte dal contenuto delle tabelle che in alcuni RCP compare due volte.

Guardando i PDF ho trovato anche la causa di buona parte di questi errori. Oltre al piè di pagina legale, AIFA stampa su ogni pagina una filigrana diagonale "Agenzia Italiana del Farmaco", grigia e semitrasparente, che attraversa tutto il testo. La filigrana è nello strato di testo del PDF, quindi i converter la leggono insieme al resto e i modelli di layout la scambiano per una parte della pagina. In un RCP di prova era il 13% del testo estratto. Docling sui PDF scaricati, per esempio, in alcuni documenti unisce tutto il testo in un solo paragrafo e riconosce come titoli solo il 7% dei titoli standard.

AIFA aggiunge il timbro dopo, in due flussi di contenuto separati da quello del produttore, il primo con il piè di pagina e il secondo con la filigrana. Lo schema è lo stesso in 269.402 pagine su 269.450 e in 18.009 PDF su 18.016. Per questo lo posso togliere in modo esatto senza cercare il testo: `conv/clean.py` svuota quei due flussi e lascia identica la pagina originale. La data "Documento reso disponibile da AIFA il …" la salvo a parte, perché dice quando AIFA ha pubblicato il documento. Tutti i converter leggono i PDF puliti, tranne s3 che resta sui PDF scaricati come nella pipeline del progetto.

## 5. Piano

| Fase | Cosa | Risultato |
|---|---|---|
| 1 | riscaricare tutti i PDF disponibili su AIFA e questa volta tenerli | `conv/data/pdf`, 18.016 PDF, 6 GB |
| 2 | scegliere un campione di prova di 40 documenti | `conv/gold.csv` |
| 3 | confrontare i converter sul campione con la H100 | scelta del converter, con i numeri |
| 4 | togliere il timbro AIFA da tutti i PDF e convertirli con il converter scelto, sulle 4 H100 se disponibili | Markdown e struttura JSON di ogni documento, con un controllo di qualità per documento |
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
| marker | 2.0.0, modalità balanced | testo del PDF più il VLM surya per layout e tabelle |
| MinerU | 4.0.12, livello advanced | VLM, con server locale e telemetria spenta |
| olmOCR | 0.4.27 | VLM (Qwen2.5-VL addestrato per l'OCR) che riceve l'immagine della pagina e il testo del PDF |

Ho fissato per ognuno l'ultima versione uscita da almeno due settimane. Ogni converter ha il suo ambiente Python, perché le dipendenze sono incompatibili tra loro (`conv/plan_bakeoff.sh`).

Farli girare ha richiesto qualche aggiustamento, tutto nello script:
- vLLM su PyPI è compilato per CUDA 13, mentre il driver della H100 arriva a CUDA 12.6. Uso le build per CUDA 12.9 delle release di vLLM con il pacchetto di compatibilità di NVIDIA;
- marker avvia il suo modello con docker, che sulla VM non c'è. Avvio io lo stesso server vLLM con gli stessi parametri e marker lo usa da lì;
- MinerU 4.0.12 chiede solo una libreria (docvortex) di versione inferiore alla 1, e il 04/11 è uscita la 0.6.13 che ne cambia l'interfaccia e fa fallire ogni conversione. Ho fissato la 0.5.16, uscita insieme a MinerU 4.0.12. Inoltre MinerU converte di default solo le prime 10 pagine e vuole il suo server acceso prima di ogni comando;
- olmOCR vuole una GPU da almeno 15 GB, quindi sulla vGPU da 12 GB non parte.

## 9. Risultati

### 9.1 Confronto sul campione

Tutti sui PDF puliti, tranne s3 che legge i PDF scaricati come nella pipeline del progetto. Tempi sulla H100 per i 40 documenti (980 pagine), modelli caricati compresi. GraniteDocling l'ho misurato solo sulla vGPU, perché è stato escluso subito.

| Converter | Recall | Precision | Recall minima | Titoli (marcati / anche senza marcatura) | Tabelle | Righe sfasate | Secondi | Secondi per pagina |
|---|---|---|---|---|---|---|---|---|
| s3 attuale | 0,994 | 0,770 | 0,926 | 21% / 22% | 226 | 16% | 292 | 0,30 |
| Docling | 0,994 | 0,990 | 0,928 | 92% / 94% | 292 | 0% | 125 | 0,13 |
| MinerU | 0,992 | 0,988 | 0,899 | 96% / 96% | 299 | 0% | 631 | 0,64 |
| olmOCR | 0,985 | 0,992 | 0,877 | 0% / 98% | 289 | 7,5% | 342 | 0,35 |
| marker | 0,976 | 0,996 | 0,886 | 91% / 92% | 285 | 0% | 1.200 | 1,22 |
| GraniteDocling (vGPU) | 0,924 | 0,983 | 0,725 | 97% / 97% | 27 | 0% | 6.499 | 6,63 |

GraniteDocling e marker li ho esclusi perché perdono testo senza segnalarlo. GraniteDocling butta intere tabelle, per esempio quella delle dosi pediatriche di un radiofarmaco, e marker salta le righe corte e alcuni sottotitoli. marker impiega lo stesso tempo sulla vGPU e sulla H100, quindi il suo limite è la CPU e non la GPU.

olmOCR scrive i titoli come righe semplici, senza segni di intestazione. Per questo ho aggiunto al punteggio una seconda misura, il titolo all'inizio di una riga con o senza marcatura. Le righe sfasate di MinerU risultavano al 42% finché il conteggio non teneva conto delle celle unite su più righe.

Ho scelto Docling come base. Ha la recall più alta, anche nel documento peggiore, è il più veloce e produce anche un JSON con la struttura di ogni pagina. MinerU è quasi pari.

### 9.2 Prove di ibrido sul campione

Prima di convertire tutto ho provato a mescolare i converter (`conv/exp/hybrid_tests.py`):
- scegliendo per ogni documento il converter con la recall più alta si passa da 0,9935 a 0,9963, e quasi tutto il guadagno viene da un solo documento;
- scegliendo per ogni sezione il guadagno viene quasi solo da titoli che un converter riconosce e l'altro no, non da testo in più;
- riconoscendo i titoli QRD nel testo e segnandoli come intestazione, i titoli trovati passano dal 92% al 94% con Docling e dallo 0% al 97% con olmOCR.

Quindi ho tenuto un converter solo come base e ho riparato quello che manca, con un ripiego sugli altri dove Docling va male.

### 9.3 Conversione completa

Docling ha convertito i 18.016 PDF puliti. 17.196 sulla vGPU, che però si è bloccata due volte (il kernel della VM si fermava, a prescindere da cosa girava), e gli altri sulle 4 H100 in 788 secondi, un quarto dei documenti per GPU. Il controllo di qualità su tutti i documenti (`conv/qc.py`) ha trovato 139 file vuoti, scritti mentre la vGPU si bloccava. Ho riconvertito tutti i documenti di quella finestra in 456 secondi.

Con il testo nativo di ogni PDF come riferimento, su tutto il corpus:

| Misura | Docling |
|---|---|
| recall media e mediana | 0,9944 e 0,9968 |
| documenti con recall sotto 0,95 | 188 (1,0%), di cui 21 sotto 0,90 |
| precision media | 0,9937 |
| titoli QRD riconosciuti | 92,9%, con la riparazione 96,9% |
| tabelle | 59.898, di cui 18.500 coppie consecutive con le stesse colonne |

I 188 documenti sotto 0,95 li ho riconvertiti con MinerU (1.928 secondi) e olmOCR (455 secondi) e per ognuno ho tenuto la versione con la recall più alta. La recall media di questi documenti passa da 0,915 a 0,983. Vince MinerU in 162 casi, olmOCR in 24 e Docling in 2.

Solo 2 PDF su 18.016 hanno il testo nativo illeggibile, cioè un font senza la tabella dei caratteri (044480, FI e RCP). Per questi serve leggere l'immagine della pagina, e MinerU e olmOCR, che lo fanno in modo indipendente, concordano su 97–99% delle parole. Negli altri pochi documenti ancora sotto 0,95 è il testo nativo ad avere parole spezzate, come "bus tina", che il converter invece ricompone.

### 9.4 Corpus finale

`conv/assemble.py` mette insieme per ogni documento la versione scelta e poi, senza modelli:
- unisce le tabelle spezzate tra due pagine, cioè due tabelle consecutive con le stesse colonne e niente testo in mezzo, togliendo l'intestazione ripetuta;
- toglie i commenti HTML, i riferimenti alle immagini e i tag che aggiunge MinerU, tenendo il testo;
- segna come intestazione le righe che sono un titolo QRD.

| Misura | Corpus finale |
|---|---|
| documenti | 18.016 (Docling 17.829, MinerU 163, olmOCR 24) |
| recall media | 0,9948 |
| documenti con recall sotto 0,95 | 10, di cui 4 sotto 0,90 |
| precision media | 0,9940 |
| titoli QRD riconosciuti | 96,9% di 302.075 |
| tabelle | 45.282, dopo averne unite 14.616 |

In 326 documenti la recall scende un po' dopo l'unione, perché le intestazioni delle tabelle ripetute a ogni pagina ci sono più volte nel testo nativo e solo una volta nel Markdown. È voluto.

### 9.5 Vocabolario e sezioni

`conv/vocab.py` divide ogni documento nelle sue sezioni e conta parole, coppie e terne per sezione. Per la sezione 1 dei FI all'inizio riconoscevo solo "Cos'è" e non "Che cos'è", e cercavo la sezione dopo l'indice iniziale nel modo sbagliato. Corretto questo, la sezione 1 c'è in 8.611 FI su 9.008 invece di 5.027.

| Misura | Valore |
|---|---|
| parole in tutto il corpus | 97 milioni |
| sezioni | 309.517 |
| sezioni QRD per documento | 26,7 negli RCP (su 28), 5,7 nei FI (su 6) |
| documenti senza modello QRD (formato vecchio) | 42 RCP e 298 FI |
| parole distinte presenti in almeno 2 documenti | 89.606 |
| parole, coppie e terne per sezione | 283.588, 449.546 e 516.355 |
| frasi identiche in almeno 20 documenti della stessa sezione | 10.330, di cui 30 in almeno metà e 6 in almeno il 90% |

Le frasi più ripetute sono formule di rito, per esempio "Potrebbe aver bisogno di leggerlo di nuovo" nel 97% dei FI. Sono poche, quindi il testo da togliere come ripetuto è poco. Le parole presenti in 2 soli documenti sono in buona parte rumore, ma ci sono anche i nomi commerciali rari, quindi non si può pulire per frequenza. La pulizia la decido più avanti, misurando l'effetto sulla ricerca.

Il vocabolario si guarda con `conv/docs/vocabulary.html`, generata da `conv/vocab_viz.py`.

`conv/split_sections.py` salva ogni sezione in un file a parte, come pezzo esatto del documento. Rimettendo insieme le sezioni in ordine si riottiene il documento identico, e lo verifico su tutti. Documenti e sezioni, con l'inizio e la fine di ogni sezione nel documento e un'impronta sha256, sono in `corpus.sqlite`.
