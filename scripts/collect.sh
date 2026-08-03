#!/usr/bin/env bash
# Собирает всё, что нужно для разбора падения, в один файл.
# Запуск:  ./scripts/collect.sh
# Потом прислать содержимое logs/collect.txt
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$DIR/logs/collect.txt"
mkdir -p "$DIR/logs"

{
  echo "=========== СОБРАНО $(date '+%F %T') ==========="
  echo
  echo "--- аптайм и нагрузка ---";            uptime
  echo; echo "--- память ---";                 free -h
  echo; echo "--- диск ---";                   df -h /
  echo; echo "--- службы ---"
  systemctl is-active gunicorn nginx fail2ban 2>&1 | paste -d' ' <(echo -e "gunicorn\nnginx\nfail2ban") -

  echo; echo "--- fail2ban: правила и баны ---"
  if command -v fail2ban-client >/dev/null 2>&1; then
    fail2ban-client status 2>&1
    for j in $(fail2ban-client status 2>/dev/null | sed -n 's/.*Jail list:\s*//p' | tr ',' ' '); do
      echo "  [$j]"; fail2ban-client status "$j" 2>&1 | sed 's/^/    /'
    done
  else
    echo "fail2ban не установлен"
  fi

  echo; echo "--- правила блокировки в firewall ---"
  iptables -S 2>/dev/null | grep -E 'j (DROP|REJECT)' | head -40 || echo "нет доступа к iptables"
  command -v ipset >/dev/null 2>&1 && ipset list -n 2>/dev/null

  echo; echo "--- журнал предыдущей загрузки: ядро ---"
  journalctl -k -b -1 --no-pager 2>/dev/null | grep -i -E 'oom|killed process|blocked for more than|nf_conntrack' | tail -20 || echo "недоступно"

  echo; echo "--- gunicorn, последние 40 строк ---"
  journalctl -u gunicorn -n 40 --no-pager 2>&1 | tail -40

  echo; echo "--- nginx error.log, последние 40 строк ---"
  tail -40 /var/log/nginx/error.log 2>/dev/null || echo "нет файла"

  echo; echo "--- fail2ban.log: последние баны ---"
  grep -i -E 'ban|found' /var/log/fail2ban.log 2>/dev/null | tail -30 || echo "нет файла"

  echo; echo "--- коды ответов в nginx за последние 2000 запросов ---"
  tail -2000 /var/log/nginx/access.log 2>/dev/null | awk '{print $9}' | sort | uniq -c | sort -rn | head

  echo; echo "--- watch.log: последние 60 замеров ---"
  tail -60 "$DIR/logs/watch.log" 2>/dev/null || echo "watch.sh не запускался"
  echo; echo "=========== КОНЕЦ ==========="
} > "$OUT" 2>&1

echo "Готово. Отправьте содержимое файла:"
echo "  cat $OUT"
