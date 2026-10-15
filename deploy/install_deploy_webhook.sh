#!/usr/bin/env bash
#
# Автодеплой: пуш в ветку — сервер сам забирает код и перезапускается,
# без ручного deploy.sh по SSH. Та же схема, что в NASLUX.
#
#   cd /var/www/s-poryadok
#   sudo bash deploy/install_deploy_webhook.sh
#
# Что делает:
#   1. заводит секрет в /etc/s-poryadok/deploy-webhook.env (если его нет);
#   2. проверяет, что сайт стоит на той ветке, которую будет выкладывать —
#      иначе первый же пуш откатил бы сайт;
#   3. ставит и запускает службу poryadok-deploy-webhook;
#   4. ставит конфиг nginx из проекта — в нём есть путь для вебхука.
#      Через scripts/nginx-setup.sh: он проверяет конфиг до включения
#      и сам возвращает прежний, если проверка не прошла;
#   5. печатает, что вписать в GitHub.
#
# Запускать повторно безопасно: секрет не перегенерируется, служба
# перезапускается с новым кодом, nginx трогается только если пути нет.

set -Eeuo pipefail

ROOT="${ROOT:-/var/www/s-poryadok}"
ETC="${ETC:-/etc/s-poryadok}"   # переопределяется для проверки на подставной папке
ENV_FILE="$ETC/deploy-webhook.env"
UNIT=poryadok-deploy-webhook
DOMAIN="${DOMAIN:-s-poryadok.ru}"

if [ -t 1 ]; then
  B=$'\e[1m'; R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; N=$'\e[0m'
else
  B=''; R=''; G=''; Y=''; N=''
fi
step() { echo -e "\n${B}── $* ${N}"; }
ok()   { echo -e "  ${G}✓${N} $*"; }
warn() { echo -e "  ${Y}!${N} $*"; }
die()  { echo -e "\n${R}✗ $*${N}\n" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "Нужны права root: sudo bash deploy/install_deploy_webhook.sh"
[ -f "$ROOT/deploy.sh" ] || die "Нет $ROOT/deploy.sh — запускайте на сервере, в папке проекта."
command -v python3 >/dev/null || die "python3 не найден."
command -v openssl >/dev/null || die "openssl не найден — им генерируется секрет."

# ── 1. Секрет ────────────────────────────────────────────────────────

step "Секрет"
mkdir -p "$ETC"
chmod 700 "$ETC"
if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT/deploy/deploy-webhook.env.example" "$ENV_FILE"
  # Заглушка в файле — дыра, а не напоминание: генерируем сразу.
  SECRET="$(openssl rand -hex 32)"
  sed -i "s|^DEPLOY_WEBHOOK_SECRET=.*|DEPLOY_WEBHOOK_SECRET=$SECRET|" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok "секрет сгенерирован и записан в $ENV_FILE"
else
  ok "файл $ENV_FILE уже есть — секрет не трогаю"
fi
SECRET="$(grep '^DEPLOY_WEBHOOK_SECRET=' "$ENV_FILE" | cut -d= -f2-)"
BRANCH="$(grep '^DEPLOY_WEBHOOK_BRANCH=' "$ENV_FILE" | cut -d= -f2- || true)"
BRANCH="${BRANCH:-main}"
case "$SECRET" in
  ""|ЗАМЕНИТЕ*) die "В $ENV_FILE не задан DEPLOY_WEBHOOK_SECRET." ;;
esac

# ── 2. Ветка ─────────────────────────────────────────────────────────
#
# Самая дорогая ошибка автодеплоя — не сломанная выкладка, а удачная
# выкладка не той ветки. Если сайт стоит на рабочей ветке, а вебхук
# настроен на main, первый пуш в main выкатит main — и всё, чего в нём
# ещё нет, исчезнет с сайта. Это уже случалось руками; повторять
# автоматически незачем.

step "Ветка"
LIVE="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
if [ "$LIVE" != "$BRANCH" ]; then
  die "Сайт сейчас стоит на ветке «$LIVE», а выкладывать будет «$BRANCH».
  Первый же пуш в «$BRANCH» заменил бы на сайте всё, что есть в «$LIVE».

  Два выхода:
    • влить «$LIVE» в «$BRANCH» на GitHub, выложить её:
          bash deploy.sh $BRANCH
      и запустить этот скрипт снова;
    • или выкладывать «$LIVE»: поменять в $ENV_FILE строку
          DEPLOY_WEBHOOK_BRANCH=$LIVE
      и запустить этот скрипт снова."
fi
ok "сайт стоит на «$LIVE», выкладываться будет она же"

# ── 3. Служба ────────────────────────────────────────────────────────

step "Служба"
cp "$ROOT/deploy/$UNIT.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null 2>&1
systemctl restart "$UNIT"
sleep 1
if systemctl is-active --quiet "$UNIT"; then
  ok "служба $UNIT работает"
else
  journalctl -u "$UNIT" -n 20 --no-pager >&2 || true
  die "Служба $UNIT не запустилась — журнал выше."
fi

# ── 4. nginx ─────────────────────────────────────────────────────────

step "nginx"
if nginx -T 2>/dev/null | grep -q 'location = /internal/deploy-webhook'; then
  ok "путь /internal/deploy-webhook уже настроен"
else
  warn "пути для вебхука нет — ставлю конфиг из проекта"
  bash "$ROOT/scripts/nginx-setup.sh" --apply
  nginx -T 2>/dev/null | grep -q 'location = /internal/deploy-webhook' \
    || die "После установки конфига пути всё ещё нет — смотрите вывод выше."
  ok "путь добавлен"
fi

# ── 5. Проверка и инструкция ─────────────────────────────────────────
#
# Стучимся сами, без подписи: ответ 401 значит, что вся цепочка живая —
# nginx видит слушателя, слушатель видит секрет и отказывает чужим.

step "Проверка"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' -H 'X-GitHub-Event: ping' \
  -d '{}' "https://$DOMAIN/internal/deploy-webhook" || true)"
case "$CODE" in
  401) ok "слушатель отвечает и без подписи отказывает — так и должно быть" ;;
  502) die "nginx путь знает, а слушатель не отвечает: journalctl -u $UNIT -n 50" ;;
  *)   warn "неожиданный ответ $CODE — проверьте вручную, см. DEPLOY.md" ;;
esac

step "Осталось добавить вебхук в GitHub"
cat <<TXT

  Репозиторий → Settings → Webhooks → Add webhook

    Payload URL:   https://$DOMAIN/internal/deploy-webhook
    Content type:  application/json
    Secret:        $SECRET
    Events:        Just the push event

  После сохранения GitHub пришлёт ping — в разделе Recent Deliveries
  у него должен быть ответ 200. Дальше каждый пуш в «$BRANCH»
  выкладывается сам, а чем кончилось — придёт в Telegram.

  Журнал:  journalctl -u $UNIT -f
TXT
