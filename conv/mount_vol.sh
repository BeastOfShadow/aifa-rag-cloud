#!/usr/bin/env bash
# Monta il volume aifa-conv-dev-data (modelli, venv, PDF puliti e primi Markdown) in /mnt/vol e lo mette nel fstab con
# nofail: senza nofail, dopo un reboot con il disco assente la VM si blocca. Se è già montato (make deploy) non fa nulla.
UUID_VOL=947d46f5-fa6b-48de-86cd-925cd0cba424
mountpoint -q /mnt/vol || sudo mount UUID=$UUID_VOL /mnt/vol || exit 1
grep -q "$UUID_VOL" /etc/fstab || echo "UUID=$UUID_VOL /mnt/vol ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab > /dev/null
