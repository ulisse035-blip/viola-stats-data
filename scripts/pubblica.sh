#!/bin/bash
# Fa il commit dei file cambiati (anche cancellati) e li pubblica sul ramo main.
set -e
git config user.name "viola-stats-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
if git diff --cached --quiet; then echo "Nessuna modifica da pubblicare"; exit 0; fi
git commit -q -m "$1 ($(TZ=Europe/Rome date '+%d/%m/%Y %H:%M'))"
for i in 1 2 3; do
  git pull -q --rebase origin main && git push -q origin HEAD:main && { echo "Pubblicato"; exit 0; }
  sleep 5
done
echo "Push non riuscito"; exit 1
