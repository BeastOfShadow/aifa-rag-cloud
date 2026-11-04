#!/usr/bin/env bash
# Confronto dei converter sul campione di prova (conv/gold.csv, 40 PDF, 980 pagine), sulla VM con la H100.
# Avviato da `make bench PLAN=conv/plan_bakeoff.sh` dopo `make conv-push`; risultati con `make conv-fetch`.
# Ogni converter ha il suo ambiente (dipendenze incompatibili tra loro) e la sua cartella conv/out/<converter>/.
# Se uno fallisce si passa al successivo: l'esito è in conv/out/times.csv e il log in conv/out/<converter>.log.
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
W=/mnt/vol/conv                      # volume: ambienti e modelli non stanno nei 40 GB del disco della VM
export HF_HOME=$W/hf UV_CACHE_DIR=$W/uv-cache
mkdir -p "$W/out" "$W/venv"
ln -sfn "$W/out" conv/out
IN=$PWD/conv/data/pdf_gold
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

# vLLM è compilato per CUDA >= 12.8. Se il driver è più vecchio (H100 intera con l'immagine CUDA: 12.6), sulle GPU
# da datacenter il pacchetto di compatibilità in avanti di NVIDIA fa girare il runtime nuovo sul driver vecchio.
# Con un driver più nuovo il pacchetto non serve, anzi romperebbe CUDA (libreria più vecchia del modulo del kernel)
sudo apt-get install -y -qq poppler-utils > /dev/null
CUDA_DRIVER=$(nvidia-smi | sed -n 's/.*CUDA Version: *\([0-9.]*\).*/\1/p')
COMPAT=
if [ "$(printf '%s\n' "$CUDA_DRIVER" 12.8 | sort -V | head -1)" != 12.8 ]; then
  sudo apt-get install -y -qq cuda-compat-12-9 > /dev/null && COMPAT=/usr/local/cuda-12.9/compat
fi
echo "driver CUDA $CUDA_DRIVER, compat: ${COMPAT:-no}"

venv() {   # venv <nome> <pacchetti...>: Python 3.12 per tutti (vLLM non ha ancora le wheel per 3.13+)
  local v=$W/venv/$1; shift
  uv venv -q --allow-existing --python 3.12 "$v" && uv pip install -q -p "$v" --torch-backend cu126 "$@"
}

run() {    # run <converter> <versione> <comando...>: tempo totale sul campione, modelli caricati compresi
  local name=$1 version=$2; shift 2
  mkdir -p "$OUT/$name"
  local t0; t0=$(date +%s)
  if "$@" > "$OUT/$name.log" 2>&1; then s=ok; else s=fail; fi
  echo "$name,$version,$(($(date +%s) - t0)),$s" >> "$TIMES"
  echo "== $name: $s in $(($(date +%s) - t0)) s"
}

# 0. riferimento: la conversione attuale di s3 (pymupdf4llm), nell'ambiente del RAG installato da make deploy
run s3 "pymupdf4llm $(.venv/bin/python -c 'import pymupdf4llm; print(pymupdf4llm.__version__)')" \
  .venv/bin/python -c "
import pathlib, sys, pymupdf4llm
for p in sorted(pathlib.Path('$IN').glob('*.pdf')):
    (pathlib.Path('$OUT/s3') / (p.stem + '.md')).write_text(pymupdf4llm.to_markdown(str(p), show_progress=False))"

# 1. Docling, pipeline standard: testo del PDF + modello di layout + TableFormer accurato, senza OCR
venv docling docling==2.141.0 && \
run docling 2.141.0 "$W/venv/docling/bin/docling" --from pdf --to md --to json --no-ocr --table-mode accurate \
  --device cuda --image-export-mode placeholder --output "$OUT/docling" "$IN"

# 2. Docling con il VLM GraniteDocling: legge le pagine come immagini
run docling-vlm 2.141.0 "$W/venv/docling/bin/docling" --from pdf --to md --to json --pipeline vlm \
  --vlm-model granite_docling --device cuda --image-export-mode placeholder --output "$OUT/docling-vlm" "$IN"

# 3. marker in modalità balanced (VLM surya per layout e tabelle, servito da vLLM in docker); senza docker
#    solo testo del PDF (--disable_ocr), e lo segno nella versione
venv marker marker-pdf==2.0.0 && \
if command -v docker > /dev/null; then
  run marker "2.0.0 balanced" env LD_LIBRARY_PATH=$COMPAT "$W/venv/marker/bin/marker" "$IN" \
    --output_dir "$OUT/marker" --output_format markdown --disable_image_extraction
else
  run marker "2.0.0 disable_ocr" "$W/venv/marker/bin/marker" "$IN" \
    --output_dir "$OUT/marker" --output_format markdown --disable_image_extraction --disable_ocr
fi

# 4. MinerU, livello advanced (VLM), server locale: niente --remote (caricherebbe i PDF sul loro cloud), telemetria spenta
venv mineru "mineru[full]==4.0.12" && (
  M=$W/venv/mineru/bin
  export LD_LIBRARY_PATH=$COMPAT
  "$M/mineru" telemetry disable && "$M/mineru-kit" models download --tier advanced && \
  "$M/mineru" config set parse_server.local.managed_tier advanced && \
  "$M/mineru" config set parse_server.local.mode managed && "$M/mineru" server start
) > "$OUT/mineru-setup.log" 2>&1 && \
run mineru "4.0.12 advanced" bash -c "for f in '$IN'/*.pdf; do
  '$W/venv/mineru/bin/mineru' parse \"\$f\" --tier advanced --wait 1800 --output '$OUT/mineru'/\$(basename \"\$f\" .pdf).md || exit 1
done"

# 5. olmOCR: VLM (Qwen2.5-VL addestrato per l'OCR) che riceve l'immagine della pagina e il testo del PDF
venv olmocr "olmocr[gpu]==0.4.27" && \
run olmocr 0.4.27 env LD_LIBRARY_PATH=$COMPAT "$W/venv/olmocr/bin/olmocr" "$W/olmocr-ws" --markdown \
  --pdfs "$IN"/*.pdf && cp -r "$W/olmocr-ws/markdown/." "$OUT/olmocr/"

cat "$TIMES"
echo BENCH_DONE
