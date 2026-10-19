# Approfondimento — Infrastructure as Code e confronto costi/prestazioni

Estensione del progetto concordata con il prof. Canonico (opzione 3):

> automatizzare il provisioning dell'infrastruttura (infrastructure as code) e confrontare costi e
> prestazioni di diverse configurazioni (solo CPU, GPU, locale e cloud).

Il punto di partenza è descritto in [LAVORO_SVOLTO.md](LAVORO_SVOLTO.md). Qui ci sono gli obiettivi,
le domande di ricerca, il disegno degli esperimenti e il piano di lavoro.

---

## 1. Da dove si parte

Oggi la pipeline è riproducibile *a livello software* (bootstrap e run sono script), ma non a livello
*infrastruttura*:

| Aspetto | Oggi | Obiettivo |
|---|---|---|
| Lease e istanza | creati a mano dalla dashboard Chameleon | creati da codice, versionati |
| Configurazione della VM | `bootstrap.sh` lanciato a mano via SSH | applicata automaticamente all'avvio |
| Dati in ingresso | riscaricati da AIFA a ogni run | letti da un object storage nel cloud |
| Configurazioni misurate | 1 (H100 + 48 vCPU) | matrice di configurazioni CPU/GPU, locale/cloud |
| Misure | tempi annotati a mano, alcune stime | raccolta automatica, ripetuta, in CSV |
| Costi | non stimati | costo per run e per fase, con modello esplicito |
| Rilascio risorse | manuale | `destroy` automatico a fine run |

---

## 2. Domande di ricerca

1. **Quanto costa rendere l'infrastruttura riproducibile?** Tempo dal comando `apply` alla VM pronta
   (provisioning + bootstrap), rispetto alla durata del lavoro utile.
2. **Qual è la configurazione migliore per ciascuna fase?** La pipeline ha fasi con profili diversi:
   - s2 download → limitato dalla rete (e dalla cortesia verso AIFA);
   - s3 PDF → Markdown con OCR → limitato dalla CPU, parallelizzabile;
   - s4 embedding → limitato dalla GPU;
   - s5 interrogazione → sensibile alla latenza, carico leggero.
3. **Una VM unica è uno spreco?** Nel run attuale la H100 è rimasta ferma durante i 37 minuti di
   conversione e i 48 vCPU sono rimasti quasi inattivi durante l'embedding. Confronto:
   **configurazione monolitica** (una VM GPU per tutto) contro **configurazione eterogenea**
   (VM solo CPU per s3, VM GPU solo per s4, dati scambiati via object storage).
4. **Quando conviene il cloud rispetto al locale?** Punto di pareggio in funzione della dimensione
   del corpus (100, 1.000, 18.000 documenti): sotto una certa soglia il tempo di provisioning e di
   trasferimento dati supera il guadagno di calcolo.
5. **Come scala s3 con i core?** Speedup ed efficienza al variare dei worker (1, 2, 4, 8, 16, 32, 48)
   e confronto con la legge di Amdahl.

---

## 3. Strumenti

### 3.1 Provisioning su Chameleon

Chameleon è basato su OpenStack, con il servizio di prenotazione **Blazar** per i lease. Opzioni:

| Strumento | Ruolo | Note |
|---|---|---|
| **Terraform** + provider OpenStack | rete, chiavi, security group, istanza, floating IP, volumi | standard industriale, stato dichiarativo, `plan`/`apply`/`destroy` |
| **python-chi** (libreria ufficiale Chameleon) oppure CLI `openstack reservation` | creazione del lease Blazar | il provider OpenStack di Terraform non gestisce i lease Blazar |
| **cloud-init** | configurazione della VM al primo avvio | sostituisce il lancio manuale di `bootstrap.sh` |
| **Ansible** (alternativa a cloud-init) | configurazione idempotente da remoto | utile se si vuole riconfigurare senza ricreare la VM |

Scelta proposta: **lease con python-chi → istanza e rete con Terraform → configurazione con
cloud-init**, tutto orchestrato da un unico comando (es. `make run CONFIG=gpu-h100`).

Da verificare nella documentazione Chameleon prima di iniziare: come si aggancia un'istanza al lease
su KVM@TACC (prenotazione di *flavor*, a differenza dei siti bare metal dove si prenotano nodi) e quali
tipi di GPU/nodi sono effettivamente prenotabili nel periodo di lavoro.

### 3.2 Dati
- **Object storage** di Chameleon (Swift, con interfaccia compatibile S3) come sorgente condivisa:
  un campione fisso di PDF caricato una volta, letto da tutte le configurazioni. Così le misure non
  dipendono dalla rete AIFA e non si ripetono migliaia di download verso il loro server.
- Nota: oggi i PDF vengono cancellati dopo la conversione (`KEEP_PDF=0`), quindi il campione va
  riscaricato una volta con `KEEP_PDF=1`.

### 3.3 Misure
- Tempo per fase: timestamp di inizio/fine scritti da ogni script in un CSV.
- Utilizzo risorse: `nvidia-smi` (utilizzo GPU, memoria, potenza in watt), `psutil`/`vmstat` (CPU, RAM),
  `powermetrics` su macOS per il consumo del Mac mini.
- Tempi di infrastruttura: durata di `apply`, tempo fino a SSH disponibile, durata del bootstrap,
  durata di `destroy`.

---

## 4. Configurazioni da confrontare

| ID | Dove | Risorse | Fasi testate |
|---|---|---|---|
| `local-cpu` | Mac mini M4 | 10 core CPU | s3, s4 (campione), s5 |
| `local-mps` | Mac mini M4 | GPU integrata (MPS) | s4 (campione), s5 |
| `cloud-cpu-s` | Chameleon KVM | VM piccola, solo CPU (es. 8 vCPU) | s3, s4 (campione), s5 |
| `cloud-cpu-l` | Chameleon KVM | VM grande, solo CPU (es. 48 vCPU) | s3, s4 (campione) |
| `cloud-gpu` | Chameleon KVM@TACC | H100 + 48 vCPU (configurazione attuale) | s3, s4, s5 |
| `cloud-gpu-alt` | Chameleon (se disponibile) | GPU di generazione precedente (es. A100/V100) | s4 |
| `cloud-split` | Chameleon | `cloud-cpu-l` per s3 + `cloud-gpu` per s4 | pipeline completa |

Per le configurazioni lente non serve il corpus completo: si usa un **campione fisso** (es. 1.000
documenti) e si estrapola; il run completo si fa solo sulle configurazioni candidate migliori.

---

## 5. Metriche

**Prestazioni**
- tempo di esecuzione per fase e totale (*wall-clock*);
- throughput: documenti/s per s3, chunk/s per s4;
- latenza di s5: mediana e 95° percentile su un insieme fisso di domande;
- speedup ed efficienza di s3 al variare dei core: `S(p) = T(1) / T(p)`, `E(p) = S(p) / p`;
- utilizzo medio di CPU e GPU per fase (misura diretta dello spreco della VM monolitica).

**Infrastruttura**
- *time-to-ready*: dal comando di provisioning alla VM pronta a lavorare;
- quota di overhead: `(provisioning + bootstrap + trasferimento dati) / tempo totale`;
- riproducibilità: ricreare da zero la configurazione e ottenere lo stesso numero di chunk e lo
  stesso risultato sulle query di prova.

**Costi**
- costo per run e per fase;
- costo per 1.000 documenti indicizzati;
- energia consumata (kWh) per run, sia in locale sia sulla GPU cloud.

---

## 6. Modello di costo

Chameleon è gratuito per ricerca e didattica (il consumo è conteggiato in *Service Unit*
dell'allocazione), quindi il costo in euro va **stimato** con un modello esplicito:

```
costo_cloud = Σ_fasi (ore_risorsa_attiva × prezzo_orario_equivalente)
              + costo_storage + costo_traffico_in_uscita
costo_locale = energia_kWh × prezzo_kWh + ammortamento_hardware × ore
```

- **Prezzo orario equivalente**: listino on-demand di istanze comparabili presso fornitori commerciali
  (es. VM con 1× H100, VM da 48 vCPU senza GPU), rilevato alla data dell'esperimento e citato con la
  fonte. Si riportano almeno due fornitori per mostrare la variabilità.
- **Ore fatturate ≠ ore utili**: si conta tutto il tempo in cui la risorsa è allocata, compresi
  provisioning, bootstrap e tempi morti. È qui che l'automazione e la configurazione eterogenea
  dovrebbero far risparmiare.
- **Traffico in uscita**: il rientro dei risultati in locale è di ~12 GB (ChromaDB ~11 GB, Markdown
  ~1 GB). Gratuito su Chameleon, a pagamento presso molti fornitori commerciali.
- **Locale**: potenza misurata con `powermetrics` × durata × prezzo dell'energia, più una quota di
  ammortamento del Mac mini.

Risultato atteso: un grafico **tempo vs costo** per ogni configurazione, con la frontiera di Pareto
(le configurazioni per cui non ne esiste una più veloce *e* più economica).

---

## 7. Ipotesi da verificare

- **H1** — La configurazione eterogenea (`cloud-split`) costa meno della monolitica a parità di
  tempo totale o quasi, perché la GPU viene pagata solo durante l'embedding.
- **H2** — s3 scala quasi linearmente fino a un certo numero di core, poi l'efficienza cala (lettura
  da disco, overhead di avvio dei processi): esiste un numero di vCPU oltre il quale non conviene
  pagare.
- **H3** — Sotto una certa dimensione del corpus il locale vince, perché l'overhead fisso del cloud
  (lease, provisioning, bootstrap, trasferimento) domina.
- **H4** — Per l'interrogazione (s5) il cloud non conviene: la latenza in locale con MPS (~4,5 s) è
  dominata dal reranker e la GPU cloud la ridurrebbe, ma al costo di una risorsa sempre accesa.

---

## 8. Protocollo sperimentale

1. Campione fisso e versionato di documenti (stessi file per tutte le configurazioni), caricato
   sull'object storage.
2. Versioni bloccate di dipendenze, modelli (bge-m3, reranker) e immagine del sistema operativo.
3. Per ogni configurazione: `provisioning → bootstrap → s3 → s4 → s5 → raccolta misure → destroy`,
   tutto automatico.
4. Almeno 3 ripetizioni per misura; si riportano mediana e variabilità.
5. Primo run a vuoto (*warm-up*) escluso dalle misure di s5 per non contare il caricamento dei modelli.
6. Tutti i risultati in CSV, grafici generati da script (come già fatto con
   [docs/generate_charts.py](docs/generate_charts.py)).
7. Rimisurare in condizioni controllate i numeri attuali ottenuti da stime o da un solo batch
   (es. throughput dell'embedding su H100).

---

## 9. Struttura del repository (proposta)

```
infra/
  terraform/            # rete, security group, istanza, floating IP (provider OpenStack)
    configs/            # un file di variabili per configurazione (cloud-cpu-s, cloud-gpu, ...)
  lease.py              # creazione/rilascio lease Blazar (python-chi)
  cloud-init.yaml       # bootstrap automatico (sostituisce chameleon/bootstrap.sh)
bench/
  run_config.sh         # provisioning → pipeline → misure → destroy
  collect.py            # tempi e utilizzo risorse → CSV
  cost_model.py         # CSV misure + listini → costi
  results/              # CSV grezzi, uno per run
Makefile                # make run CONFIG=..., make destroy, make report
```

---

## 10. Piano di lavoro

| Fase | Attività | Risultato |
|---|---|---|
| 1 | Studio di python-chi, Terraform OpenStack e lease su KVM@TACC; prova su una VM piccola senza GPU | `apply`/`destroy` funzionanti |
| 2 | cloud-init al posto di `bootstrap.sh`; campione PDF su object storage | VM pronta senza intervento manuale |
| 3 | Strumentazione degli script (tempi e utilizzo risorse in CSV) | misure automatiche |
| 4 | Esperimenti sulla matrice di configurazioni, scaling di s3 | CSV dei risultati |
| 5 | Configurazione eterogenea `cloud-split` | confronto monolitica vs eterogenea |
| 6 | Modello di costo, grafici, analisi delle ipotesi | relazione finale |

---

## 11. Rischi

- **Disponibilità delle GPU su Chameleon**: le H100 sono richieste; prenotare i lease con anticipo
  e prevedere una GPU alternativa.
- **Quota dell'allocazione** (Service Unit): stimare il consumo prima degli esperimenti più lunghi e
  usare il campione per le configurazioni lente.
- **Differenze tra siti** (KVM vs bare metal): se le configurazioni sono su siti diversi, il
  confronto va dichiarato con le sue differenze (virtualizzazione, rete, disco).
- **Variabilità della VM**: le VM condivise possono avere prestazioni variabili; per questo servono
  ripetizioni.
- **Prezzi commerciali** che cambiano nel tempo: annotare data e fonte di ogni listino usato.

---

## 12. Schema per la presentazione

1. Problema: RAG su ~18.000 documenti AIFA, fasi con profili di carico diversi.
2. Prima versione: cloud usato "a mano" come calcolo a noleggio (risultati del run H100).
3. Limiti: provisioning manuale, una sola configurazione, costi ignoti, risorse sprecate.
4. Infrastructure as code: architettura (lease → Terraform → cloud-init → pipeline → destroy).
5. Esperimenti: matrice di configurazioni, scaling di s3, monolitica vs eterogenea.
6. Risultati: tempo vs costo, frontiera di Pareto, punto di pareggio locale/cloud.
7. Conclusioni: quale configurazione per quale fase, e quanto costa la riproducibilità.
