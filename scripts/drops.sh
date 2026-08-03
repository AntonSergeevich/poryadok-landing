#!/usr/bin/env bash
# Не теряет ли пакеты сам сервер.
#
# Перехват показывает только то, что доехало до сетевой карты. Если пакет
# отбрасывает сама карта, драйвер или очередь ядра — в перехвате его не
# будет, и со стороны это неотличимо от потери в сети. Этот скрипт
# смотрит счётчики, которые ведёт ядро.
#
#   sudo bash scripts/drops.sh

set -u
OUT="$(cd "$(dirname "$0")/.." && pwd)/logs/drops.txt"
mkdir -p "$(dirname "$OUT")"
say() { echo -e "\n=== $* ==="; }

{
echo "Снято: $(date '+%F %T %Z')"

say "Счётчики сетевой карты"
ip -s -s link show 2>/dev/null | grep -A4 -E '^[0-9]+: (eth|ens|enp)' | head -30
echo
echo "Смотреть на dropped и errors в строках RX. Ноль — карта ничего не теряет."

say "Потери в очередях ядра"
if command -v nstat >/dev/null 2>&1; then
  nstat -az 2>/dev/null | grep -E \
    'ListenOverflows|ListenDrops|RcvPruned|OfoPruned|TCPRcvQDrop|TCPBacklogDrop|TCPOFODrop|NoPorts|InCsumErrors' \
    | awk '{ printf "   %-28s %s\n", $1, $2 }'
else
  netstat -s 2>/dev/null | grep -i -E 'listen|pruned|overflow|discard' | sed 's/^/   /'
fi
echo
echo "Все нули — ядро ничего не отбрасывает, и потеря происходит ДО сервера."
echo "Ненулевые ListenOverflows или BacklogDrop — сервер не успевает"
echo "принимать соединения, и это уже чинится на месте."

say "Очередь ожидающих соединений"
echo "   somaxconn = $(sysctl -n net.core.somaxconn 2>/dev/null)"
echo "   tcp_max_syn_backlog = $(sysctl -n net.ipv4.tcp_max_syn_backlog 2>/dev/null)"
echo "   nginx backlog: $(grep -rhoE 'backlog=[0-9]+' /etc/nginx/ 2>/dev/null | head -1 || echo 'по умолчанию 511')"

say "Кто больше всех открывает соединений прямо сейчас"
ss -tn state established '( sport = :443 or sport = :80 )' 2>/dev/null \
  | awk 'NR>1{ ip=$4; sub(/:[0-9]+$/,"",ip); print ip }' | sort | uniq -c | sort -rn | head -10 \
  || echo "   нет установленных соединений"

say "Проверка ограничений на частоту в nginx"
grep -rhE 'limit_req|limit_conn' /etc/nginx/ 2>/dev/null | sed 's/^/   /' || echo "   ограничений нет"
} 2>&1 | tee "$OUT"

echo
echo "Сохранено в $OUT"
