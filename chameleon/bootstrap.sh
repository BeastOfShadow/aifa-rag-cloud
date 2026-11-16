#!/usr/bin/env bash
# Bootstrap nodo Chameleon (Ubuntu 24.04), VM con o senza GPU: stesso Python e stesse librerie del Mac.
# Uso:  bash bootstrap.sh            (override build torch: TORCH_INDEX=cu128 bash bootstrap.sh)
set -euo pipefail

echo "== 1. GPU / driver =="
if command -v nvidia-smi >/dev/null; then
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
    # build CUDA 12.6 (driver CUDA 12.6 del nodo H100). cu124 non ha più versioni recenti,
    # cu128/cu130 richiedono un driver più nuovo
    TORCH_INDEX="${TORCH_INDEX:-cu126}"
else
    echo "nvidia-smi assente: VM solo CPU, torch build CPU"
    TORCH_INDEX="${TORCH_INDEX:-cpu}"
fi

echo "== 2. deps di sistema (OCR incluso) =="
sudo apt-get update -qq
sudo apt-get install -y -qq git rsync curl ocrmypdf tesseract-ocr tesseract-ocr-ita

echo "== 3. Python 3.13.4 + venv (uv: stessa versione del Mac, non il Python di Ubuntu) =="
cd "$(dirname "$0")/.."          # -> radice progetto (aifa-rag-cloud/)
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/0.11.28/install.sh | sh
uv venv -q --allow-existing --python 3.13.4 .venv   # un secondo deploy sulla stessa VM riusa il venv
. .venv/bin/activate
# stessa versione fissata in requirements.txt, presa dall'indice della build giusta;
# constraints.txt fissa anche le dipendenze indirette (transformers, numpy, ...)
TORCH_VERSION=$(sed -n 's/^torch==\([0-9.]*\).*/\1/p' rag/requirements.txt)
uv pip install -q --index-url "https://download.pytorch.org/whl/$TORCH_INDEX" "torch==$TORCH_VERSION"
uv pip install -q -r rag/requirements.txt -c rag/constraints.txt

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
