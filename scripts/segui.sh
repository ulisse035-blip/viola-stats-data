#!/bin/bash
# Segue la partita in corso: controlla ogni 2 minuti finche' la partita non e' salvata come finale
# (o finche' non c'e' piu' una partita in corso). Cosi' l'intervallo e la fine non dipendono dalla
# puntualita' del cron di GitHub, che spesso parte con molto ritardo.
MODO="${1:-auto}"
FINE=$(( $(date +%s) + ${MINUTI:-230} * 60 ))
while :; do
  git pull -q --rebase origin main || true
  OUT=$(python3 scripts/viola.py "$MODO" 2>&1); echo "$OUT"
  bash scripts/pubblica.sh "partita: $MODO" || true
  [ "$MODO" != auto ] && exit 0
  echo "$OUT" | grep -q -e "nessuna partita della Fiorentina in corso" -e "partita gia' salvata come finale" && exit 0
  echo "$OUT" | grep -q -e "\[FT\]" && exit 0
  [ $(date +%s) -ge $FINE ] && exit 0
  sleep 120
done
