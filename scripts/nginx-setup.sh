#!/usr/bin/env bash
#
# Поставить конфигурацию nginx из репозитория.
#
#   sudo bash scripts/nginx-setup.sh            показать, что сейчас живёт
#   sudo bash scripts/nginx-setup.sh --apply    поставить конфиг из проекта
#
# Зачем скрипт, а не список команд.
#
# Здесь ломается HTTPS. Не «может сломаться» — ломается: путь к
# сертификату отличается на один символ, nginx перезагружается с битым
# конфигом, и сайт перестаёт открываться целиком. Причём не сразу:
# `systemctl reload` при ошибке молча оставляет работать старый конфиг,
# и человек уходит, думая, что всё применилось.
#
# Скрипт делает три вещи, которые руками делают редко:
#   1. показывает, какой файл на самом деле работает — а не какой
#      предполагается;
#   2. проверяет новый конфиг ДО того, как он станет рабочим;
#   3. если проверка не прошла — сам возвращает прежний.
#
# Ничего не удаляет: прежний конфиг остаётся копией с датой.

set -Eeuo pipefail

ROOT="${ROOT:-/var/www/s-poryadok}"
DOMAIN="${DOMAIN:-s-poryadok.ru}"
SRC="$ROOT/deploy/nginx/s-poryadok.conf"
AVAILABLE="${AVAILABLE:-/etc/nginx/sites-available}"
ENABLED="${ENABLED:-/etc/nginx/sites-enabled}"
# Пути переопределяются переменными: так скрипт можно прогнать
# на подставном окружении, не трогая рабочий сервер.
LE="${LE:-/etc/letsencrypt}"
NAME="${NAME:-s-poryadok}"

if [ -t 1 ]; then
  B=$'\e[1m'; R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; N=$'\e[0m'
else
  B=''; R=''; G=''; Y=''; N=''
fi
step() { echo -e "\n${B}── $* ${N}"; }
ok()   { echo -e "  ${G}✓${N} $*"; }
warn() { echo -e "  ${Y}!${N} $*"; }
die()  { echo -e "\n${R}✗ $*${N}\n" >&2; exit 1; }

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

[ "$(id -u)" = "0" ] || die "Нужны права root: sudo bash scripts/nginx-setup.sh"
[ -f "$SRC" ] || die "Нет файла $SRC — запускайте из папки проекта."
command -v nginx >/dev/null || die "nginx не установлен."

# ── Что работает сейчас ──────────────────────────────────────────────
#
# Не «что должно работать», а что nginx реально прочитал. Разница между
# этими двумя вещами и есть причина, по которой открытый файл оказался
# пустым: конфиг живёт под другим именем.

step "Что nginx читает сейчас"

echo "  Включённые файлы:"
ls -1 "$ENABLED" 2>/dev/null | sed 's/^/    /' || echo "    (пусто)"

echo
echo "  Где описан $DOMAIN:"
nginx -T 2>/dev/null | awk -v d="$DOMAIN" '
  /^# configuration file/ { file = $4 }
  $0 ~ "server_name.*" d { print "    " file }
' | sort -u || true

if nginx -T 2>/dev/null | grep -q 'location /media/'; then
  ok "раздача /media/ уже есть"
  MEDIA_OK=1
else
  warn "раздачи /media/ нет — вложения в переписке не откроются"
  MEDIA_OK=0
fi

# ── Чем отличается от репозитория ────────────────────────────────────

step "Разница с конфигом из проекта"

LIVE=""
for candidate in "$AVAILABLE/$NAME" "$AVAILABLE/$NAME.conf" "$AVAILABLE/default"; do
  if [ -f "$candidate" ] && grep -q "$DOMAIN" "$candidate" 2>/dev/null; then
    LIVE="$candidate"
    break
  fi
done

if [ -n "$LIVE" ]; then
  ok "рабочий файл: $LIVE"
  if diff -q "$LIVE" "$SRC" >/dev/null 2>&1; then
    ok "совпадает с проектом — менять нечего"
    # Условие отдельным if, а не «[ … ] && exit 0»: при set -e ложное
    # условие само по себе даёт код возврата 1 и обрывает скрипт там,
    # где надо было просто идти дальше.
    if [ "$MEDIA_OK" = 1 ]; then
      exit 0
    fi
  else
    echo "  Отличия (слева — сервер, справа — проект):"
    diff --unified=1 "$LIVE" "$SRC" | head -60 | sed 's/^/    /' || true
  fi
else
  warn "файла с $DOMAIN в $AVAILABLE не нашёл — поставлю новый"
fi

# ── Сертификаты ──────────────────────────────────────────────────────
#
# Главная опасность. Конфиг из репозитория ссылается на конкретные пути;
# если их нет, nginx не запустится, и сайт умрёт целиком.

step "Сертификаты"

# Пути берём только из настоящих директив, а не отовсюду, где встретилась
# строка. В конфиге есть комментарий, объясняющий, почему
# options-ssl-nginx.conf сознательно НЕ подключается, — и простой поиск
# по тексту принимал его за обязательный файл: скрипт отказывался
# ставить рабочий конфиг из-за упоминания в комментарии.
MISSING=0
while IFS= read -r cert; do
  [ -n "$cert" ] || continue
  if [ -f "$cert" ]; then
    ok "есть: $cert"
  else
    warn "НЕТ: $cert"
    MISSING=1
  fi
done < <(sed 's/#.*//' "$SRC" \
         | grep -oE '(ssl_certificate|ssl_certificate_key|ssl_dhparam|include)[[:space:]]+/etc/letsencrypt/[^ ;]+' \
         | awk '{print $2}' | sed "s|^/etc/letsencrypt|$LE|" | sort -u)

if [ "$MISSING" = 1 ]; then
  die "Часть сертификатов не найдена. Ставить конфиг нельзя — nginx
не поднимется, и сайт перестанет открываться целиком.

Обычно это значит, что сертификат лежит под другим именем. Посмотрите:
    ls -la $LE/live/
и поправьте пути в $SRC"
fi

if [ "$APPLY" != 1 ]; then
  echo
  echo -e "${B}Ничего не менял. Чтобы поставить:${N}"
  echo "    sudo bash scripts/nginx-setup.sh --apply"
  echo
  exit 0
fi

# ── Установка ────────────────────────────────────────────────────────

step "Ставлю"

STAMP="$(date +%F-%H%M)"
if [ -n "$LIVE" ]; then
  cp "$LIVE" "$LIVE.backup-$STAMP"
  ok "прежний сохранён: $LIVE.backup-$STAMP"
  TARGET="$LIVE"
else
  TARGET="$AVAILABLE/$NAME.conf"
fi

cp "$SRC" "$TARGET"
ok "положил в $TARGET"

ln -sf "$TARGET" "$ENABLED/$(basename "$TARGET")"
ok "включил ссылкой из $ENABLED"

# Два включённых файла с одним server_name — это война за домен, где
# побеждает тот, что прочитан первым. Отключаем лишние.
for link in "$ENABLED"/*; do
  [ -e "$link" ] || continue
  [ "$(readlink -f "$link")" = "$(readlink -f "$TARGET")" ] && continue
  if grep -q "$DOMAIN" "$(readlink -f "$link")" 2>/dev/null; then
    warn "отключаю дубль: $link"
    rm -f "$link"
  fi
done

# ── Проверка до включения ────────────────────────────────────────────
#
# nginx -t читает конфиг, не применяя его. Если здесь ошибка — возвращаем
# прежний файл и не трогаем работающий сервер.

step "Проверка конфига"

if ! nginx -t 2>&1 | sed 's/^/  /'; then
  if [ -n "$LIVE" ] && [ -f "$LIVE.backup-$STAMP" ]; then
    cp "$LIVE.backup-$STAMP" "$TARGET"
    warn "вернул прежний конфиг"
  else
    rm -f "$TARGET" "$ENABLED/$(basename "$TARGET")"
    warn "убрал новый файл"
  fi
  die "Конфиг с ошибкой — не применял. Сайт работает на прежнем.
Текст ошибки выше: в нём указан файл и номер строки."
fi
ok "конфиг без ошибок"

systemctl reload nginx
ok "nginx перечитал конфиг"

# ── Что получилось ───────────────────────────────────────────────────

step "Проверяю на живом сервере"

for path in / /static/landing/css/site.css; do
  CODE="$(curl -sS -o /dev/null --max-time 10 -w '%{http_code}' \
          -H "Host: $DOMAIN" "http://127.0.0.1$path" 2>/dev/null || true)"
  CODE="${CODE:-000}"
  case "$CODE" in
    200|301|302) ok "$path — $CODE" ;;
    *) warn "$path — $CODE" ;;
  esac
done

if nginx -T 2>/dev/null | grep -q 'location /media/'; then
  ok "раздача /media/ на месте"
else
  warn "раздачи /media/ всё ещё нет — посмотрите $TARGET"
fi

echo
echo -e "${G}${B}Готово.${N}"
if [ -n "$LIVE" ]; then
  echo "Откат: sudo cp $LIVE.backup-$STAMP $TARGET && sudo systemctl reload nginx"
fi
echo
