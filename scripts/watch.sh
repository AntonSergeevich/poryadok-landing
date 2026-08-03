#!/usr/bin/env bash
# Снимает состояние сервера раз в 30 секунд в logs/watch.log.
# Нужен, чтобы поймать момент, когда сайт перестаёт отвечать: после
# перезагрузки dmesg очищается, а этот файл остаётся на диске.
#
# Запуск в фоне (переживает выход из ssh):
#   nohup /var/www/s-poryadok/scripts/watch.sh >/dev/null 2>&1 &
# Остановить:
#   pkill -f scripts/watch.sh
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$DIR/logs/watch.log"
mkdir -p "$DIR/logs"

while true; do
  TS="$(date '+%Y-%m-%d %H:%M:%S')"

  # Отвечает ли приложение через сокет, и за сколько
  CODE_TIME="$(curl -s -o /dev/null -w '%{http_code} %{time_total}' \
      --max-time 15 --unix-socket /run/gunicorn.sock http://localhost/ 2>/dev/null || echo 'нет-ответа -')"

  # Отвечает ли сайт снаружи, через nginx
  EXT="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -k https://127.0.0.1/ \
      -H 'Host: s-poryadok.ru' 2>/dev/null || echo 'нет-ответа')"

  # Забанен ли кто-то прямо сейчас: правила блокировки и списки fail2ban
  BANS="$(iptables -S 2>/dev/null | grep -c -E 'j (DROP|REJECT)')"
  F2B="$(fail2ban-client banned 2>/dev/null | tr -d '\n' | cut -c1-120)"
  [ -z "$F2B" ] && F2B='—'

  MEM="$(free -m | awk '/^Mem:/{printf "%s/%sМБ доступно %s", $3, $2, $7}')"
  SWAP="$(free -m | awk '/^Swap:/{printf "%s/%s", $3, $2}')"
  LOAD="$(cut -d' ' -f1-3 /proc/loadavg)"
  DISK="$(df -h / | awk 'NR==2{print $5" занято, "$4" свободно"}')"
  GUNI="$(ps --no-headers -o rss -C python3 2>/dev/null | awk '{s+=$1} END{printf "%.0fМБ", s/1024}')"
  PROCS="$(pgrep -c -f 'gunicorn' 2>/dev/null || echo 0)"
  CONN="$(ss -H -tan state established 2>/dev/null | wc -l)"

  printf '%s | сокет %s | nginx %s | память %s | swap %s | load %s | диск %s | gunicorn %s (%s проц.) | соединений %s | блокировок %s | забанены %s\n' \
    "$TS" "$CODE_TIME" "$EXT" "$MEM" "$SWAP" "$LOAD" "$DISK" "$GUNI" "$PROCS" "$CONN" "$BANS" "$F2B" >> "$LOG"

  # Файл не должен расти бесконечно
  if [ "$(wc -l < "$LOG")" -gt 20000 ]; then
    tail -n 5000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  fi

  sleep 30
done
