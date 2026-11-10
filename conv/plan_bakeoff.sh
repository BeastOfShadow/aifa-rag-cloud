#!/usr/bin/env bash
# Confronto dei converter sul campione di prova (conv/gold.csv, 40 PDF, 980 pagine), sulla VM con la H100,
# sui PDF senza il timbro AIFA.
# Avviato da `make bench PLAN=conv/plan_bakeoff.sh` dopo `make conv-push`; risultati con `make conv-fetch`.
# Ogni converter ha il suo ambiente (dipendenze incompatibili tra loro) e la sua cartella conv/out/<converter>/.
# Se uno fallisce si passa al successivo: l'esito è in conv/out/times.csv e il log in conv/out/<converter>.log.
# RUN=<nome>: cartella dei risultati sul volume (default out), per non sovrascrivere un'esecuzione precedente;
# SKIP_VLM=1: salta GraniteDocling (già escluso: butta tabelle e testo, e su GPU piccola è lentissimo).
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
W=/mnt/vol/conv                      # volume: ambienti e modelli non stanno nei 40 GB del disco della VM
export HF_HOME=$W/hf UV_CACHE_DIR=$W/uv-cache
mkdir -p "$W/${RUN:-out}" "$W/venv"
ln -sfn "$W/${RUN:-out}" conv/out
RAW=$PWD/conv/data/pdf_gold
IN=$PWD/conv/data/pdf_gold_clean      # senza il timbro AIFA (conv/clean.py): lo leggono tutti i converter tranne s3
OUT=$PWD/conv/out
TIMES=$OUT/times.csv
[ -f "$TIMES" ] || echo "converter,version,seconds,status" > "$TIMES"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
# sulla vGPU (immagine CC-Ubuntu24.04-CUDA-VGPU) il modulo uvm in /lib/modules è della build "open" del driver,
# mentre quello caricato è il proprietario: modprobe fallisce e CUDA non parte finché non carico l'uvm proprietario
if [ ! -e /dev/nvidia-uvm ]; then
  sudo modprobe nvidia-uvm 2>/dev/null || \
    sudo insmod "$(ls /var/lib/dkms/nvidia/*/"$(uname -r)"/x86_64/module/nvidia-uvm.ko* | grep -v -- -open/ | head -1)"
  sudo nvidia-modprobe -u -c=0
fi

# vLLM su PyPI è compilato per CUDA 13; uso le build per CUDA 12.9 delle release su GitHub, con torch per 12.9.
# Se il driver è più vecchio (H100 intera con l'immagine CUDA: 12.6), sulle GPU da datacenter il pacchetto di
# compatibilità in avanti di NVIDIA fa girare il runtime nuovo sul driver vecchio. Con un driver più nuovo il pacchetto
# non serve, anzi romperebbe CUDA (libreria più vecchia del modulo del kernel)
VLLM=https://github.com/vllm-project/vllm/releases/download
sudo apt-get install -y -qq poppler-utils > /dev/null
CUDA_DRIVER=$(nvidia-smi | sed -n 's/.*CUDA Version: *\([0-9.]*\).*/\1/p')
COMPAT=
if [ "$(printf '%s\n' "$CUDA_DRIVER" 12.9 | sort -V | head -1)" != 12.9 ]; then
  sudo apt-get install -y -qq cuda-compat-12-9 > /dev/null && COMPAT=/usr/local/cuda-12.9/compat
fi
echo "driver CUDA $CUDA_DRIVER, compat: ${COMPAT:-no}"

venv() {   # [TORCH=cu129] venv <nome> <pacchetti...>: Python 3.12 per tutti (vLLM non ha ancora le wheel per 3.13+)
  local v=$W/venv/$1; shift
  uv venv -q --allow-existing --python 3.12 "$v" && uv pip install -q -p "$v" --torch-backend "${TORCH:-cu126}" "$@"
}

run() {    # run <converter> <versione> <comando...>: tempo totale sul campione, modelli caricati compresi
  local name=$1 version=$2; shift 2
  mkdir -p "$OUT/$name"
  local t0; t0=$(date +%s)
  if "$@" > "$OUT/$name.log" 2>&1; then s=ok; else s=fail; fi
  echo "$name,$version,$(($(date +%s) - t0)),$s" >> "$TIMES"
  echo "== $name: $s in $(($(date +%s) - t0)) s"
}

.venv/bin/python conv/clean.py "$RAW" "$IN"

# 0. riferimento: la conversione attuale di s3 (pymupdf4llm) sui PDF scaricati, nell'ambiente del RAG di make deploy
run s3 "pymupdf4llm $(.venv/bin/python -c 'import pymupdf4llm; print(pymupdf4llm.__version__)')" \
  .venv/bin/python -c "
import pathlib, sys, pymupdf4llm
for p in sorted(pathlib.Path('$RAW').glob('*.pdf')):
    (pathlib.Path('$OUT/s3') / (p.stem + '.md')).write_text(pymupdf4llm.to_markdown(str(p), show_progress=False))"

# 1. Docling, pipeline standard: testo del PDF + modello di layout + TableFormer accurato, senza OCR
venv docling docling==2.141.0 && \
run docling 2.141.0 "$W/venv/docling/bin/docling" --from pdf --to md --to json --no-ocr --table-mode accurate \
  --device cuda --image-export-mode placeholder --output "$OUT/docling" "$IN"

# 2. Docling con il VLM GraniteDocling: legge le pagine come immagini
[ -n "$SKIP_VLM" ] || run docling-vlm 2.141.0 "$W/venv/docling/bin/docling" --from pdf --to md --to json --pipeline vlm \
  --vlm-model granite_docling --device cuda --image-export-mode placeholder --output "$OUT/docling-vlm" "$IN"

# 3. marker in modalità balanced: VLM surya per layout, OCR e tabelle. marker lo avvierebbe con l'immagine docker di
#    vLLM, ma sulla VM docker non c'è: lo servo io con la stessa versione di vLLM e gli stessi parametri (quelli di surya
#    per una GPU da 80 o da 16 GB) e marker lo usa tramite SURYA_INFERENCE_URL. Il kernel GDN di flashinfer non si
#    compila con il nvcc 12.6 dell'immagine, quindi uso quello triton. Il tempo non comprende l'avvio di vLLM
S=$W/venv/surya-vllm/bin
venv marker marker-pdf==2.0.0 && \
TORCH=cu129 venv surya-vllm "vllm @ $VLLM/v0.20.1/vllm-0.20.1+cu129-cp38-abi3-manylinux_2_31_x86_64.whl" \
  transformers==5.12.1 && {
  if [ "$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)" -gt 40000 ]; then
    SEQS=104 TOKENS=16384 UTIL=0.85
  else
    SEQS=16 TOKENS=4096 UTIL=0.6
  fi
  env LD_LIBRARY_PATH=$COMPAT PATH="$S:$PATH" "$S/vllm" serve datalab-to/surya-ocr-2 --port 8100 \
    --served-model-name datalab-to/surya-ocr-2 --no-enforce-eager --dtype bfloat16 --max-model-len 18000 \
    --max-num-seqs $SEQS --max-num-batched-tokens $TOKENS --gpu-memory-utilization $UTIL --enable-prefix-caching \
    --gdn-prefill-backend triton --mm-processor-kwargs '{"min_pixels": 3136, "max_pixels": 6291456}' \
    --speculative-config '{"method": "mtp", "num_speculative_tokens": 2}' > "$OUT/surya-vllm.log" 2>&1 &
  for _ in $(seq 90); do curl -sf http://127.0.0.1:8100/health > /dev/null && break; sleep 10; done
  run marker "2.0.0 balanced" env SURYA_INFERENCE_URL=http://127.0.0.1:8100/v1 "$W/venv/marker/bin/marker" "$IN" \
    --output_dir "$OUT/marker" --output_format markdown --disable_image_extraction --mode balanced
  kill $! $(pgrep -f "m surya\.") 2>/dev/null    # vLLM e i server di supporto che marker lascia accesi
  sleep 15
}

# 4. MinerU, livello advanced (VLM servito da vLLM), server locale. Il server arriva al livello standard, che accetta
#    anche le richieste advanced (stessi modelli, più sforzo). Ogni comando di mineru vuole il server acceso: lo avvio e
#    spengo subito la telemetria, che invia solo dati raccolti durante il parsing, e punto l'URL del servizio remoto a
#    una porta chiusa, così nessun PDF esce dalla VM. Il tempo misurato non comprende l'avvio del server (~2,5 min).
#    Con la config di un'esecuzione precedente il server parte già al livello standard, e config set mentre carica
#    vLLM va in timeout: prima aspetto che sia pronto (su una VM nuova il ciclo arriva in fondo e si prosegue)
M=$W/venv/mineru/bin
# docvortex (estrazione del testo nativo) è vincolato solo a <1: le 0.6 hanno cambiato l'interfaccia e il parse
# fallisce, quindi fisso la versione uscita con MinerU 4.0.12
TORCH=cu129 venv mineru "mineru[full]==4.0.12" docvortex==0.5.16 "vllm @ $VLLM/v0.28.0/vllm-0.28.0+cu129-cp38-abi3-manylinux_2_28_x86_64.whl" && (
  export LD_LIBRARY_PATH=$COMPAT PATH=$M:$PATH        # vLLM compila al volo con ninja, che sta nell'ambiente
  healthy() { "$M/mineru" server status --json | python3 -c \
    "import json, sys; sys.exit(not json.load(sys.stdin)['parse_server']['local']['healthy'])"; }
  "$M/mineru" server start && "$M/mineru" telemetry disable && \
  "$M/mineru" config set parse_server.remote.url http://127.0.0.1:9 && \
  "$M/mineru-kit" models download --tier standard && \
  for _ in $(seq 30); do healthy && break; sleep 10; done && \
  "$M/mineru" config set parse_server.local.managed_tier standard && \
  "$M/mineru" config set parse_server.local.mode managed && "$M/mineru" server restart && \
  for _ in $(seq 90); do healthy && break; sleep 10; done && healthy
) > "$OUT/mineru-setup.log" 2>&1 && \
run mineru "4.0.12 advanced" bash -c "for f in '$IN'/*.pdf; do   # senza --pages converte solo le prime 10 pagine
  '$M/mineru' parse \"\$f\" --tier advanced --pages all --wait 1800 --output '$OUT/mineru'/\$(basename \"\$f\" .pdf).md || exit 1
done"
"$M/mineru" server stop > /dev/null 2>&1    # libera la GPU per olmOCR

# 5. olmOCR: VLM (Qwen2.5-VL addestrato per l'OCR) che riceve l'immagine della pagina e il testo del PDF; vuole una GPU
#    da almeno 15 GB, quindi non gira sulla vGPU da 12 GB
TORCH=cu129 venv olmocr "olmocr[gpu]==0.4.27" "vllm @ $VLLM/v0.11.2/vllm-0.11.2+cu129-cp38-abi3-manylinux1_x86_64.whl" && \
run olmocr 0.4.27 env LD_LIBRARY_PATH=$COMPAT PATH="$W/venv/olmocr/bin:$PATH" "$W/venv/olmocr/bin/olmocr" \
  "$W/olmocr-ws" --markdown --pdfs "$IN"/*.pdf && cp -r "$W/olmocr-ws/markdown/." "$OUT/olmocr/"

cat "$TIMES"
echo BENCH_DONE
