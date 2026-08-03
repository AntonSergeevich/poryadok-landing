#!/usr/bin/env bash
# Следит за счётчиком повреждённых пакетов и отмечает момент каждой порчи.
#
# Ядро считает пакеты, пришедшие с битой контрольной суммой, и молча их
# выбрасывает. Для отправителя это неотличимо от потери. Если счётчик
# растёт ровно тогда, когда сайт отваливается, — причина найдена.
#
#   sudo bash scripts/csum.sh &        следить в фоне
#   tail -f logs/csum.log              смотреть, что накопилось
#
# Останов: pkill -f scripts/csum.sh

set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$DIR/logs/csum.log"
mkdir -p "$DIR/logs"

read_c() { nstat -az 2>/dev/null | awk '/TcpInCsumErrors/{print $2; exit}'; }

PREV="$(read_c)"; PREV="${PREV:-0}"
echo "$(date '+%F %T') старт, повреждённых пакетов накоплено: $PREV" | tee -a "$LOG"
echo "Пишу в $LOG. Отмечаю только изменения." | tee -a "$LOG"

while sleep 10; do
  NOW="$(read_c)"; NOW="${NOW:-$PREV}"
  if [ "$NOW" != "$PREV" ]; then
    D=$(( NOW - PREV ))
    echo "$(date '+%F %T')  +$D повреждённых (всего $NOW)" | tee -a "$LOG"
    PREV="$NOW"
  fi
done
