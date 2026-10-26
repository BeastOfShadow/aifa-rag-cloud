#!/usr/bin/env bash
# Bootstrap nodo Chameleon (Ubuntu 22.04/24.04), VM con o senza GPU.
# Uso:  bash bootstrap.sh            (override build torch: TORCH_INDEX=cu128 bash bootstrap.sh)
set -euo pipefail

echo "== 1. GPU / driver =="
if command -v nvidia-smi >/dev/null; then
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
    # build CUDA 12.4 (compatibile driver 560/CUDA 12.6). NON il default cu130 (troppo nuovo)
    TORCH_INDEX="${TORCH_INDEX:-cu124}"
else
    echo "nvidia-smi assente: VM solo CPU, torch build CPU"
    TORCH_INDEX="${TORCH_INDEX:-cpu}"
fi

echo "== 2. deps di sistema (OCR incluso) =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git rsync \
    ocrmypdf tesseract-ocr tesseract-ocr-ita

echo "== 3. venv + pip =="
cd "$(dirname "$0")/.."          # -> radice progetto (aifa-rag-cloud/)
python3 -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q --index-url "https://download.pytorch.org/whl/$TORCH_INDEX" torch
pip install -q -r rag/requirements.txt

echo "== 4. verifica CUDA in torch =="
python - <<'PY'
import torch
print("torch", torch.__version__, "| cuda:", torch.cuda.is_available(),
      "|", (torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NO GPU"))
PY

echo
echo "OK. Ora il full run:"
echo "  . .venv/bin/activate"
echo "  cd rag"
echo "  export PILOT_N=0 EMBED_BATCH=256      # 0 = tutti i farmaci, batch grande per H100"
echo "  python s1_structured.py"
echo "  python s2_download.py                 # ~25k PDF (rete TACC veloce)"
echo "  python s3_pdf_to_md.py                # md + OCR mirato sui scansionati"
echo "  python s4_index.py                    # embed bge-m3 su GPU -> Chroma"
