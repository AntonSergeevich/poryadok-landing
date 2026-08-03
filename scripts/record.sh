#!/usr/bin/env bash
# Непрерывная запись сетевого разговора по кольцу.
#
# Отказ прерывистый: он случается, когда его никто не ловит. Ждать его
# с секундомером бессмысленно. Поэтому пишем всегда, а старое затираем —
# на диске лежит последний час, и когда сайт в очередной раз отвалится,
# доказательство уже записано.
#
#   sudo bash scripts/record.sh start    начать запись
#   sudo bash scripts/record.sh status   идёт ли запись
#   sudo bash scripts/record.sh stop     остановить
#   sudo bash scripts/record.sh save     отложить текущие файлы в сторону
#
# Занимает не больше 120 МБ: 6 файлов по 20 МБ, по кругу.
# Пишутся только заголовки пакетов (первые 128 байт), не содержимое
# страниц и не пароли.

set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
CAP="$DIR/logs/cap"
PIDF="$DIR/logs/record.pid"
CMD="${1:-status}"

running() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }

case "$CMD" in
  start)
    if running; then echo "Запись уже идёт, номер процесса $(cat "$PIDF")"; exit 0; fi
    command -v tcpdump >/dev/null 2>&1 || { echo "Нужен tcpdump: sudo apt install tcpdump"; exit 1; }
    mkdir -p "$CAP"
    nohup tcpdump -i any -nn -s 128 -W 6 -C 20 \
          -w "$CAP/web.pcap" 'tcp port 443 or tcp port 80' \
          >/dev/null 2>&1 &
    echo $! > "$PIDF"
    sleep 1
    if running; then
      echo "Запись пошла. Номер процесса $(cat "$PIDF")."
      echo "Файлы: $CAP/web.pcap*  (6 штук по 20 МБ, по кругу)"
      echo
      echo "Когда сайт в следующий раз отвалится — заходите по ssh и выполняйте:"
      echo "  sudo bash scripts/record.sh save"
    else
      echo "Не запустилось."; rm -f "$PIDF"; exit 1
    fi
    ;;

  stop)
    if running; then kill "$(cat "$PIDF")" && echo "Остановлено."; else echo "Запись не идёт."; fi
    rm -f "$PIDF"
    ;;

  save)
    STAMP="$(date '+%Y%m%d-%H%M%S')"
    KEEP="$DIR/logs/cap-$STAMP"
    mkdir -p "$KEEP"
    cp "$CAP"/web.pcap* "$KEEP"/ 2>/dev/null \
      && echo "Отложено в $KEEP" \
      || { echo "Нечего откладывать — запись не велась."; exit 1; }
    echo "Отметка времени отказа: $(date '+%F %T %Z')"
    echo "Запись продолжается. Скажите Клоду про $KEEP."
    ;;

  status)
    if running; then
      echo "Запись идёт, номер процесса $(cat "$PIDF")."
    else
      echo "Запись НЕ идёт. Запустить: sudo bash scripts/record.sh start"
    fi
    du -sh "$CAP" 2>/dev/null || true
    ls -lh "$CAP" 2>/dev/null | tail -8 || true
    ;;

  *)
    echo "Использование: sudo bash scripts/record.sh start|stop|status|save"
    exit 1
    ;;
esac
