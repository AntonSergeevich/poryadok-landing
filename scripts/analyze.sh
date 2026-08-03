#!/usr/bin/env bash
# Разбор отложенной записи: что происходило в момент отказа.
#
#   sudo bash scripts/analyze.sh logs/cap-20260803-154456
#   sudo bash scripts/analyze.sh logs/cap-20260803-154456 15:30 15:50
#
# Второй и третий доводы — окно времени (часы:минуты), чтобы отсечь всё
# лишнее и смотреть только на минуты отказа.

set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-}"
FROM="${2:-}"
TILL="${3:-}"

[ -z "$SRC" ] && { echo "Укажите папку: sudo bash scripts/analyze.sh logs/cap-ГГГГММДД-ЧЧММСС"; exit 1; }
[ -d "$SRC" ] || SRC="$DIR/$SRC"
[ -d "$SRC" ] || { echo "Нет такой папки: $SRC"; exit 1; }

FILES=$(ls "$SRC"/web.pcap* 2>/dev/null)
[ -z "$FILES" ] && { echo "В папке нет файлов записи."; exit 1; }

OUT="$DIR/logs/analyze.txt"
TXT="$(mktemp)"
trap 'rm -f "$TXT"' EXIT

echo "Читаю записи..."
for f in $FILES; do
  tcpdump -r "$f" -nn -tttt 2>/dev/null
done | sort > "$TXT"

if [ -n "$FROM" ]; then
  awk -v a="$FROM" -v b="${TILL:-23:59}" '{ t=substr($2,1,5); if (t>=a && t<=b) print }' "$TXT" > "$TXT.w"
  mv "$TXT.w" "$TXT"
fi

{
echo "Разбор записи: $SRC"
echo "Снят: $(date '+%F %T %Z')"
[ -n "$FROM" ] && echo "Окно времени: $FROM — ${TILL:-конец}"
echo "Строк для разбора: $(wc -l < "$TXT")"
[ -s "$TXT" ] || { echo; echo "В этом окне пусто. Попробуйте без окна времени."; exit 0; }
echo "Первая запись: $(head -1 "$TXT" | cut -d' ' -f1,2)"
echo "Последняя:     $(tail -1 "$TXT" | cut -d' ' -f1,2)"

# Серверная сторона узнаётся по порту 443 или 80 — какой IP там стоит,
# знать не нужно. Клиент — второй конец разговора.
AWKBODY='
function endp(s){ sub(/:$/,"",s); return s }
function isserver(s){ return (s ~ /\.443$/ || s ~ /\.80$/) }
{
  ipix=0
  for (i=1;i<=NF;i++) if ($i=="IP") { ipix=i; break }
  if (!ipix) next
  src=endp($(ipix+1)); dst=endp($(ipix+3))
  if (src=="" || dst=="") next
  if (isserver(src))      { c=dst; out=1 }
  else if (isserver(dst)) { c=src; out=0 }
  else next

  len=0
  if (match($0,/length [0-9]+$/)) len=substr($0,RSTART+7)+0

  seen[c]=1
  ip=c; sub(/\.[0-9]+$/,"",ip)
  conns[c]=ip
  if ($0 ~ /Flags \[S[EW]*\]/) syn[c]++
  if ($0 ~ /Flags \[S\./)      sak[c]++
  if (out) outb[c]+=len; else inb[c]+=len
}
END{
  for (c in seen) {
    ip=conns[c]
    total[ip]++
    if (outb[c]>0 && inb[c]>0)      { ok[ip]++;    verdict[c]="обмен состоялся" }
    else if (inb[c]>0)              { stall[ip]++; verdict[c]="СЕРВЕР НЕ ОТВЕТИЛ" }
    else if (syn[c]>0 && sak[c]==0) { nosyn[ip]++; verdict[c]="сервер не согласился" }
    else                            { empty[ip]++; verdict[c]="без данных" }
  }

  print ""
  print "== ИТОГ ПО КАЖДОМУ ПОСЕТИТЕЛЮ =="
  printf "%-20s %8s %10s %10s %10s\n", "адрес", "всего", "работали", "молчание", "пусто"
  for (ip in total)
    printf "%-20s %8d %10d %10d %10d\n", ip, total[ip], ok[ip]+0, stall[ip]+0, empty[ip]+nosyn[ip]+0

  print ""
  print "== ОТКАЗАВШИЕ СОЕДИНЕНИЯ: на скольких байтах встали =="
  for (c in seen) if (verdict[c]=="СЕРВЕР НЕ ОТВЕТИЛ") h[inb[c]]++
  for (b in h) printf "   принято %6d байт — %d соединений\n", b, h[b]

  print ""
  print "== РАБОТАВШИЕ СОЕДИНЕНИЯ: сколько прошло =="
  for (c in seen) if (verdict[c]=="обмен состоялся")
    printf "   %-24s принято %6d, отдано %8d\n", c, inb[c], outb[c]
}'

awk "$AWKBODY" "$TXT" | head -60

echo
echo "== ЧТО ЭТО ЗНАЧИТ =="
cat <<'TXT'
Если у одного адреса много «молчание» и мало «работали» — до сервера
доходит только начало разговора. Столбец «на скольких байтах встали»
показывает, где именно обрывается: одинаковое число у всех означает
границу одного сетевого пакета, то есть теряется продолжение.

Если у другого адреса в те же минуты «работали» — сервер исправен,
и дело в пути к конкретному посетителю.
TXT
} 2>&1 | tee "$OUT"

echo
echo "Сохранено в $OUT — можно скинуть целиком."
