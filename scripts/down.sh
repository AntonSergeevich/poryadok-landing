#!/usr/bin/env bash
# Запускать НА СЕРВЕРЕ в тот момент, когда сайт не открывается.
#
# Смысл: сайт — это четыре слоя, вложенных друг в друга. Скрипт стучится
# в каждый по очереди, снаружи внутрь. Первый слой, который не ответил,
# и есть сломанный. Это единственный способ не гадать.
#
#   1. Django через gunicorn   — сокет /run/gunicorn.sock
#   2. nginx по HTTP           — 127.0.0.1:80
#   3. nginx по HTTPS          — 127.0.0.1:443, то есть сертификат и TLS
#   4. сам себя снаружи        — через публичный адрес, то есть сеть хостера
#
#   Использование: sudo bash scripts/down.sh

set -u
DOMAIN="${DOMAIN:-s-poryadok.ru}"
SOCK="${SOCK:-/run/gunicorn.sock}"
OUT="$(dirname "$0")/../logs/down.txt"
mkdir -p "$(dirname "$OUT")"

say() { echo -e "\n=== $* ===" ; }

{
echo "Отчёт снят: $(date '+%F %T %Z')"
echo "Домен: $DOMAIN"

say "СЛОЙ 1. Django через gunicorn (сокет)"
curl -sS -o /dev/null --max-time 10 \
     -w 'код %{http_code}, время %{time_total}s\n' \
     --unix-socket "$SOCK" "http://localhost/" \
  || echo 'НЕ ОТВЕТИЛ — виноват Django или gunicorn'

say "СЛОЙ 2. nginx по HTTP на себя"
curl -sS -o /dev/null --max-time 10 \
     -w 'код %{http_code}, время %{time_total}s\n' \
     -H "Host: $DOMAIN" "http://127.0.0.1/" \
  || echo 'НЕ ОТВЕТИЛ — виноват nginx'

say "СЛОЙ 3. nginx по HTTPS на себя (сертификат и TLS)"
curl -sSk -o /dev/null --max-time 15 \
     -w 'код %{http_code}, время %{time_total}s, рукопожатие %{time_appconnect}s\n' \
     --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/" \
  || echo 'НЕ ОТВЕТИЛ — виноват TLS: сертификат или конфиг nginx'

say "СЛОЙ 4. сам себя через публичный адрес (сеть хостера)"
curl -sS -o /dev/null --max-time 15 \
     -w 'код %{http_code}, время %{time_total}s\n' \
     "https://$DOMAIN/" \
  || echo 'НЕ ОТВЕТИЛ — слои 1-3 живы, значит режется снаружи, на стороне хостера'

say "Кто слушает 80 и 443"
ss -ltnp 2>/dev/null | grep -E ':80 |:443 ' || echo 'НИКТО НЕ СЛУШАЕТ — nginx не запущен'

say "Состояние служб"
systemctl is-active nginx gunicorn 2>&1
systemctl status gunicorn --no-pager -n 5 2>&1 | tail -8

say "Срок сертификата"
echo | openssl s_client -connect 127.0.0.1:443 -servername "$DOMAIN" 2>/dev/null \
  | openssl x509 -noout -dates 2>/dev/null || echo 'сертификат не отдался'

say "Соединения"
ss -s 2>/dev/null | head -5
echo "в TIME-WAIT: $(ss -tan 2>/dev/null | grep -c TIME-WAIT)"
echo "установлено: $(ss -tan 2>/dev/null | grep -c ESTAB)"

say "Таблица отслеживания соединений (переполнение = сеть встаёт)"
if [ -r /proc/sys/net/netfilter/nf_conntrack_count ]; then
  echo "занято: $(cat /proc/sys/net/netfilter/nf_conntrack_count) из $(cat /proc/sys/net/netfilter/nf_conntrack_max)"
else
  echo 'счётчик недоступен'
fi

say "Правила файрвола (не появилось ли лишнего)"
echo "-- filter INPUT:"
iptables -L INPUT -n --line-numbers 2>/dev/null | head -25
echo "-- mangle POSTROUTING (сюда добавлялся TCPMSS):"
iptables -t mangle -L POSTROUTING -n --line-numbers 2>/dev/null | head -15

say "Память и место"
free -h 2>/dev/null | head -3
df -h / 2>/dev/null | tail -1

say "Хвост журнала nginx"
tail -25 /var/log/nginx/error.log 2>/dev/null || echo 'журнал недоступен'

say "Хвост журнала gunicorn"
journalctl -u gunicorn -n 25 --no-pager 2>/dev/null | tail -25

say "Ошибки ядра за последний час"
journalctl -k --since '1 hour ago' --no-pager 2>/dev/null \
  | grep -i -E 'oom|killed process|conntrack|nf_conntrack: table full|link down' \
  | tail -15 || true
echo '(пусто — значит ядро молчит, это хорошо)'

echo -e "\n=== Конец отчёта ==="
} 2>&1 | tee "$OUT"

echo
echo "Сохранено в $OUT — можно скинуть целиком."
