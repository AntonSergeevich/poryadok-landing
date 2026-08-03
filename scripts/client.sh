#!/usr/bin/env bash
# Что на самом деле получил посетитель.
#
# Журнал gunicorn показывает только, что Django ДОСЧИТАЛ страницу и отдал
# её nginx. Дошла ли она до браузера — знает лишь nginx. Поэтому смотрим
# именно его журнал: там видно, сколько байт реально ушло в сеть и не
# оборвал ли клиент соединение, не дождавшись ответа (код 499).
#
#   Использование: sudo bash scripts/client.sh
#                  sudo bash scripts/client.sh 2.26.13.3    # только свой адрес

set -u
LOG="${LOG:-/var/log/nginx/access.log}"
ONLY="${1:-}"
OUT="$(dirname "$0")/../logs/client.txt"
mkdir -p "$(dirname "$OUT")"

say() { echo -e "\n=== $* ===" ; }

{
echo "Отчёт снят: $(date '+%F %T %Z')"
[ -n "$ONLY" ] && echo "Фильтр по адресу: $ONLY"

if [ ! -r "$LOG" ]; then
  echo "ЖУРНАЛ $LOG НЕДОСТУПЕН — запускать через sudo"
  exit 1
fi

feed() { if [ -n "$ONLY" ]; then grep -F "$ONLY" "$LOG"; else cat "$LOG"; fi ; }

say "Коды ответов за весь журнал"
feed | awk '{print $9}' | sort | uniq -c | sort -rn | head -12
echo
echo "499 = браузер закрыл соединение, не дождавшись ответа."
echo "     Много таких — ответ не доходит до посетителя."

say "Последние 25 заходов живых людей (боты отброшены)"
feed \
  | grep -v -i -E 'bot|crawl|spider|measurement|curl|wget|monitor|scan' \
  | tail -25 \
  | awk '{ printf "%-16s %-22s %-28s код %-4s отдано %8s байт\n", $1, $4, $7, $9, $10 }'

say "Сколько байт отдаётся на главную странице"
feed | grep -E '"GET / HTTP' | awk '{print $10}' | sort -n | uniq -c | tail -10
echo
echo "Django отдаёт около 39 000 байт. Если здесь числа заметно меньше —"
echo "значит включено сжатие. Если встречаются обрывочные значения, не"
echo "совпадающие с обычным размером, — ответ рвётся на полпути."

say "Сжатие включено?"
grep -rE '^\s*gzip\s+(on|off)' /etc/nginx/nginx.conf /etc/nginx/conf.d/ /etc/nginx/sites-enabled/ 2>/dev/null \
  || echo 'директива gzip не найдена'
echo "-- проверка живьём:"
curl -sS -o /dev/null -D - --max-time 10 -H 'Accept-Encoding: gzip' \
     --resolve "s-poryadok.ru:443:127.0.0.1" "https://s-poryadok.ru/" 2>/dev/null \
  | grep -i -E 'content-encoding|content-length' || echo 'заголовков сжатия нет'

say "Размер пакета на сетевой карте"
ip link show 2>/dev/null | grep -E '^[0-9]+: ' | awk '{print $2, $3, $4, $5}'
echo "обычное значение mtu 1500"

say "Настройка подбора размера пакета"
echo "tcp_mtu_probing = $(sysctl -n net.ipv4.tcp_mtu_probing 2>/dev/null)  (нужно 1)"

say "Ошибки и потери на сетевой карте"
ip -s link show 2>/dev/null | grep -A2 -E 'RX:|TX:' | head -12

echo -e "\n=== Конец отчёта ==="
} 2>&1 | tee "$OUT"

echo
echo "Сохранено в $OUT"
