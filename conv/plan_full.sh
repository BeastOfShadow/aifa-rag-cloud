#!/usr/bin/env bash
# Conversione completa con Docling, sulla VM con la GPU: scarica tutti i PDF da AIFA (s2 del progetto, sul DB di s1
# senza la tabella docs in /mnt/vol/conv/data/farmaci.sqlite), toglie il timbro AIFA (conv/clean.py) e converte con le
# stesse opzioni del confronto (conv/plan_bakeoff.sh, che crea anche l'ambiente di Docling). Con più GPU i documenti
# da convertire si dividono in tanti pezzi quante sono, uno per GPU.
# Si può rilanciare: salta i passi finiti e Docling i documenti già convertiti. Dopo un reboot della VM il volume va
# rimontato (conv/mount_vol.sh, che lo mette nel fstab con nofail: la VM si blocca se il disco manca).
# Uso sulla VM: bash conv/plan_full.sh > ~/full.log 2>&1   (in tmux); risultati in /mnt/vol/conv/full/
cd "$(dirname "$0")/.."
W=/mnt/vol/conv
D=$W/data
OUT=$W/full
export HF_HOME=$W/hf
bash conv/mount_vol.sh || exit 1
mkdir -p "$OUT/docling"
t0=$(date +%s)
# s2 e clean si saltano se hanno già finito (clean scrive stamp.csv alla fine): s2 ritenta per ore i 7.182 documenti
# che AIFA non pubblica
[ -f "$D/pdf_clean/stamp.csv" ] || { DATA_DIR=$D PILOT_N=0 .venv/bin/python rag/s2_download.py
  t1=$(date +%s); .venv/bin/python conv/clean.py "$D/pdf" "$D/pdf_clean"; }
t1=${t1:-$(date +%s)}
t2=$(date +%s)
# cuda-compat come in plan_bakeoff.sh se il driver è più vecchio di 12.9 (H100 intera con l'immagine CUDA: 12.6)
CUDA_DRIVER=$(nvidia-smi | sed -n 's/.*CUDA Version: *\([0-9.]*\).*/\1/p')
[ "$(printf '%s\n' "$CUDA_DRIVER" 12.9 | sort -V | head -1)" != 12.9 ] && [ -d /usr/local/cuda-12.9/compat ] && \
  export LD_LIBRARY_PATH=/usr/local/cuda-12.9/compat
# documenti da convertire, divisi in N pezzi (uno per GPU) a blocchi di 500: ogni blocco ricarica i modelli (~20 s)
# ma un errore non fa ripartire da capo
N=$(nvidia-smi -L | wc -l)
for f in "$D"/pdf_clean/*.pdf; do b=$(basename "$f" .pdf); [ -f "$OUT/docling/$b.md" ] || echo "$f"; done \
  | split -n r/$N -d -a 1 - "$OUT/todo."
for t in "$OUT"/todo.*; do
  CUDA_VISIBLE_DEVICES=${t##*.} xargs -d '\n' -n 500 < "$t" \
    "$W/venv/docling/bin/docling" --from pdf --to md --to json --no-ocr --table-mode accurate \
    --device cuda --image-export-mode placeholder --output "$OUT/docling" &
done
wait
rm -f "$OUT"/todo.*
t3=$(date +%s)
echo "step,seconds,gpus" > "$OUT/times.csv"
printf 's2,%s,\nclean,%s,\ndocling,%s,%s\n' $((t1 - t0)) $((t2 - t1)) $((t3 - t2)) $N >> "$OUT/times.csv"
echo "md: $(ls "$OUT/docling" | grep -c '\.md$') di $(ls "$D/pdf_clean" | grep -c '\.pdf$') PDF"
echo FULL_DONE
