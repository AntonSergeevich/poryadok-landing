#!/usr/bin/env bash
#
# Перенос базы с SQLite на PostgreSQL.
#
#   sudo bash scripts/to-postgres.sh            посмотреть, что будет
#   sudo bash scripts/to-postgres.sh --apply    перенести
#
# Почему переносить стоит сейчас
#
# SQLite пишет с блокировкой всей базы: пока идёт одна запись, остальные
# ждут. Пока клиентов двое — незаметно. Когда в кабинете сидят несколько
# человек и кто-то отправляет файл, остальные упираются в паузу.
# Проявляется это не отказом, а «сайт задумался», и причину ищут где
# угодно, только не в базе.
#
# И главное: цена переноса растёт со временем. Сегодня в базе десяток
# записей, и ошибиться негде. Через полгода там живые проекты, переписка
# и оплаты — и та же операция станет тем, что делают ночью.
#
# Что делает скрипт
#
#   1. проверяет, что PostgreSQL установлен и отвечает
#   2. заводит базу и пользователя, если их нет
#   3. выгружает данные из SQLite в файл
#   4. накатывает миграции на пустой PostgreSQL
#   5. загружает данные
#   6. СЧИТАЕТ ЗАПИСИ В ОБЕИХ БАЗАХ И СРАВНИВАЕТ
#   7. дописывает DATABASE_URL в .env
#
# Шестой пункт — главный. Перенос, который «прошёл без ошибок», но потерял
# половину записей, выглядит точно так же, как удачный. Пока числа
# не сошлись, .env не трогается и сайт продолжает работать на SQLite.
#
# Откат: убрать строку DATABASE_URL из .env и перезапустить gunicorn.
# Файл db.sqlite3 остаётся нетронутым — из него только читали.

set -Eeuo pipefail

if [ -z "${PG_FROM_COPY:-}" ]; then
  SELF="$(mktemp)"
  cat "$0" > "$SELF"
  export PG_FROM_COPY=1
  bash "$SELF" "$@"
  CODE=$?
  rm -f "$SELF"
  exit $CODE
fi

ROOT="${ROOT:-/var/www/s-poryadok}"
VENV="${VENV:-$ROOT/venv}"
DB_NAME="${DB_NAME:-poryadok}"
DB_USER="${DB_USER:-poryadok}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DUMP="$ROOT/backups/dump-$(date +%F-%H%M).json"

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

[ -d "$ROOT/.git" ] || die "В $ROOT нет проекта."
cd "$ROOT"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"
[ -x "$PY" ] || die "Нет окружения в $VENV."

# ── 1. Что есть ──────────────────────────────────────────────────────

step "Проверки"

if grep -q '^DATABASE_URL=' .env 2>/dev/null; then
  die "В .env уже есть DATABASE_URL — похоже, перенос уже делали.
Если хотите начать заново, уберите эту строку и повторите."
fi
ok "DATABASE_URL в .env ещё нет"

[ -f "$ROOT/db.sqlite3" ] || die "Нет файла db.sqlite3 — переносить нечего."
ok "SQLite на месте ($(du -h db.sqlite3 | cut -f1))"

if ! command -v psql >/dev/null 2>&1; then
  warn "PostgreSQL не установлен"
  echo "      поставить:  sudo apt update && sudo apt install -y postgresql"
  [ "$APPLY" = 1 ] && die "Без PostgreSQL переносить некуда."
else
  ok "psql есть: $(psql --version | head -1)"
fi

if command -v pg_isready >/dev/null 2>&1 && pg_isready -q -h "$DB_HOST" -p "$DB_PORT"; then
  ok "сервер отвечает на $DB_HOST:$DB_PORT"
elif [ "$APPLY" = 1 ]; then
  die "PostgreSQL не отвечает на $DB_HOST:$DB_PORT.
Запустить:  sudo systemctl enable --now postgresql"
fi

# Сколько записей сейчас — это же число должно получиться потом.
step "Что переносим"
BEFORE="$($PY - <<'PYCODE'
import django, os, sys
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
os.environ.pop('DATABASE_URL', None)
django.setup()
from django.apps import apps

# Считаем ровно то, что переносим. Права и типы содержимого Django
# создаёт сам при миграции, сессии не переносятся вовсе — если считать
# их наравне с остальным, числа не сойдутся на исправном переносе,
# и скрипт заругается там, где всё хорошо.
SKIP = {'contenttypes.ContentType', 'auth.Permission', 'sessions.Session'}

total = 0
rows = []
for model in apps.get_models():
    if model._meta.label in SKIP:
        continue
    n = model.objects.count()
    total += n
    if n:
        rows.append(f'{model._meta.label}={n}')
print(total)
print(' '.join(rows))
PYCODE
)"
COUNT_BEFORE="$(echo "$BEFORE" | head -1)"
echo "  записей всего: $COUNT_BEFORE"
echo "$BEFORE" | tail -1 | tr ' ' '\n' | sed 's/^/    /'

if [ "$APPLY" != 1 ]; then
  echo
  echo -e "${B}Ничего не менял. Чтобы перенести:${N}"
  echo "    sudo bash scripts/to-postgres.sh --apply"
  echo
  exit 0
fi

# ── 2. Драйвер ───────────────────────────────────────────────────────

step "Драйвер"
if $PY -c 'import psycopg' 2>/dev/null; then
  ok "psycopg уже стоит"
else
  $PIP install -q 'psycopg[binary]>=3.1'
  ok "psycopg поставлен"
fi

# ── 3. База и пользователь ───────────────────────────────────────────

step "База и пользователь"

DB_PASS="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)"

if sudo -u postgres psql -tAc \
     "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  ok "пользователь $DB_USER уже есть — меняю пароль"
  sudo -u postgres psql -q -c \
    "ALTER ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASS';"
else
  sudo -u postgres psql -q -c \
    "CREATE ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASS';"
  ok "пользователь $DB_USER заведён"
fi

if sudo -u postgres psql -tAc \
     "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  ok "база $DB_NAME уже есть"
else
  # Кодировка и порядок сравнения задаются при создании и потом
  # не меняются. Ошибиться здесь — значит однажды получить неверную
  # сортировку русских имён и переделывать всё заново.
  sudo -u postgres createdb -O "$DB_USER" -E UTF8 \
       --lc-collate=C.UTF-8 --lc-ctype=C.UTF-8 -T template0 "$DB_NAME"
  ok "база $DB_NAME создана"
fi

URL="postgres://$DB_USER:$DB_PASS@$DB_HOST:$DB_PORT/$DB_NAME"

# ── 4. Выгрузка из SQLite ────────────────────────────────────────────

step "Выгружаю из SQLite"

mkdir -p "$ROOT/backups"
# Типы содержимого и права исключаем: Django создаёт их сам при миграции,
# и загруженные поверх дадут конфликт по уникальности.
env -u DATABASE_URL "$PY" manage.py dumpdata \
    --natural-foreign --natural-primary \
    --exclude contenttypes --exclude auth.Permission \
    --exclude sessions.session \
    --indent 1 -o "$DUMP"
ok "выгружено в $DUMP ($(du -h "$DUMP" | cut -f1))"

# ── 5. Миграции и загрузка ───────────────────────────────────────────

step "Готовлю PostgreSQL"

DATABASE_URL="$URL" "$PY" manage.py migrate --noinput 2>&1 | tail -3 | sed 's/^/  /'
ok "структура создана"

step "Загружаю данные"
DATABASE_URL="$URL" "$PY" manage.py loaddata "$DUMP" 2>&1 | tail -2 | sed 's/^/  /'

# ── 6. Сверка ────────────────────────────────────────────────────────
#
# Главный шаг. Перенос, потерявший половину записей, снаружи выглядит
# точно так же, как удачный.

step "Сверяю"

COUNT_AFTER="$(DATABASE_URL="$URL" "$PY" - <<'PYCODE'
import django, os, sys
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()
from django.apps import apps

# Считаем ровно то, что переносим. Права и типы содержимого Django
# создаёт сам при миграции, сессии не переносятся вовсе — если считать
# их наравне с остальным, числа не сойдутся на исправном переносе,
# и скрипт заругается там, где всё хорошо.
SKIP = {'contenttypes.ContentType', 'auth.Permission', 'sessions.Session'}

print(sum(m.objects.count() for m in apps.get_models()
          if m._meta.label not in SKIP))
PYCODE
)"

echo "  было в SQLite:     $COUNT_BEFORE"
echo "  стало в PostgreSQL: $COUNT_AFTER"

if [ "$COUNT_BEFORE" != "$COUNT_AFTER" ]; then
  die "Числа не сошлись. .env не трогал — сайт работает на SQLite,
данные целы.

Выгрузка осталась в $DUMP, её можно разобрать руками.
Убрать неудачную базу:  sudo -u postgres dropdb $DB_NAME"
fi
ok "сошлось"

# ── 7. Переключаем ───────────────────────────────────────────────────

step "Переключаю"

cp .env ".env.backup-$(date +%F-%H%M)"
printf '\n# База. Убрать строку — вернётся SQLite.\nDATABASE_URL=%s\n' "$URL" >> .env
ok "DATABASE_URL дописан в .env"

systemctl restart gunicorn 2>/dev/null && ok "gunicorn перезапущен" \
  || warn "gunicorn перезапустите сами: sudo systemctl restart gunicorn"

sleep 3
CODE="$(curl -sS -o /dev/null --max-time 10 -w '%{http_code}' \
        -H "Host: ${DOMAIN:-s-poryadok.ru}" \
        --unix-socket "${SOCK:-/run/gunicorn.sock}" http://localhost/ 2>/dev/null || true)"
if [ "${CODE:-000}" = "200" ]; then
  ok "сайт отвечает: 200"
else
  warn "сайт отвечает ${CODE:-000} — проверьте logs/app.log"
  echo "      вернуться на SQLite: убрать строку DATABASE_URL из .env"
  echo "      и sudo systemctl restart gunicorn"
fi

echo
echo -e "${G}${B}Готово. База теперь PostgreSQL.${N}"
echo "Файл db.sqlite3 не тронут — из него только читали."
echo "Откат: убрать строку DATABASE_URL из .env и перезапустить gunicorn."
echo
echo -e "${Y}Дальше стоит настроить копии PostgreSQL:${N}"
echo "    sudo -u postgres pg_dump $DB_NAME | gzip > backup.sql.gz"
echo
