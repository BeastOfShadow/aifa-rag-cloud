#!/usr/bin/env bash
# Conversione completa con Docling, sulla VM con la GPU: scarica tutti i PDF da AIFA (s2 del progetto, sul DB di s1
# senza la tabella docs in /mnt/vol/conv/data/farmaci.sqlite), toglie il timbro AIFA (conv/clean.py) e converte con le
# stesse opzioni del confronto (conv/plan_bakeoff.sh, che crea anche l'ambiente di Docling).
# Si può rilanciare: salta i passi finiti e Docling i documenti già convertiti. Dopo un reboot della VM il volume va
# rimontato a mano (sudo mount /dev/vdb /mnt/vol, non è nel fstab).
# Uso sulla VM: bash conv/plan_full.sh > ~/full.log 2>&1   (in tmux); risultati in /mnt/vol/conv/full/
cd "$(dirname "$0")/.."
W=/mnt/vol/conv
D=$W/data
OUT=$W/full
export HF_HOME=$W/hf
mkdir -p "$OUT/docling"
t0=$(date +%s)
# s2 e clean si saltano se hanno già finito (clean scrive stamp.csv alla fine): s2 ritenta per ore i 7.182 documenti
# che AIFA non pubblica
[ -f "$D/pdf_clean/stamp.csv" ] || { DATA_DIR=$D PILOT_N=0 .venv/bin/python rag/s2_download.py
  t1=$(date +%s); .venv/bin/python conv/clean.py "$D/pdf" "$D/pdf_clean"; }
t1=${t1:-$(date +%s)}
t2=$(date +%s)
# a blocchi di 500 documenti: ogni blocco ricarica i modelli (~20 s) ma un errore non fa ripartire da capo
for f in "$D"/pdf_clean/*.pdf; do b=$(basename "$f" .pdf); [ -f "$OUT/docling/$b.md" ] || echo "$f"; done | \
  xargs -d '\n' -n 500 "$W/venv/docling/bin/docling" --from pdf --to md --to json --no-ocr --table-mode accurate \
    --device cuda --image-export-mode placeholder --output "$OUT/docling"
t3=$(date +%s)
echo "step,seconds" > "$OUT/times.csv"
printf 's2,%s\nclean,%s\ndocling,%s\n' $((t1 - t0)) $((t2 - t1)) $((t3 - t2)) >> "$OUT/times.csv"
echo "md: $(ls "$OUT/docling" | grep -c '\.md$') di $(ls "$D/pdf_clean" | grep -c '\.pdf$') PDF"
echo FULL_DONE
