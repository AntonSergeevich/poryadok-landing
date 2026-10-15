#!/usr/bin/env python3
"""
Автодеплой: GitHub прислал пуш в нужную ветку — запускаем deploy.sh.

Та же схема, что в NASLUX, и по тем же причинам.

Это отдельный процесс, а не часть Django. Выкладке нужны права root —
перезапустить gunicorn, перечитать nginx, сменить владельца папки, —
а программа, которая разбирает чужой ввод (формы, вебхуки оплат,
загруженные файлы), не должна параллельно уметь исполнять код от root:
одна дыра в сайте стала бы дырой во всём сервере. Поэтому здесь только
стандартная библиотека, один секрет в отдельном файле и один
фиксированный скрипт — вся поверхность атаки видна в этом файле целиком.

Слушает только 127.0.0.1. Наружу его пускает nginx одним путём —
/internal/deploy-webhook, см. deploy/nginx/s-poryadok.conf.

Чем отличается от NASLUX: по окончании пишет в Telegram, чем кончилось.
deploy.sh останавливается на первом отказе и оставляет сайт на прежней
версии — это безопасно, но тихо. Без сообщения о провале выходило бы
ровно то, с чем этот проект воюет: пуш прошёл, в GitHub всё зелёное,
а на сайте старый код, и никто об этом не знает.

Установка — deploy/install_deploy_webhook.sh, подробности в DEPLOY.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ.get("DEPLOY_WEBHOOK_SECRET", "")
BRANCH = os.environ.get("DEPLOY_WEBHOOK_BRANCH", "main")
PORT = int(os.environ.get("DEPLOY_WEBHOOK_PORT", "8098"))
ROOT = os.environ.get("DEPLOY_ROOT", "/var/www/s-poryadok")
DEPLOY_SCRIPT = os.environ.get("DEPLOY_SCRIPT", os.path.join(ROOT, "deploy.sh"))

_running: subprocess.Popen | None = None
_lock = threading.Lock()


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """
    Проверить X-Hub-Signature-256 от GitHub.

    Сравнение постоянного времени: иначе по скорости ответа можно было бы
    подобрать подпись по одному байту.
    """
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def should_deploy(event: str | None, payload: dict, branch: str = BRANCH) -> tuple[bool, str]:
    """
    Разворачивать ли этот вебхук. Возвращает (да/нет, причина).

    GitHub шлёт «ping» сразу при добавлении вебхука — на него отвечаем
    без выкладки, иначе сама настройка уже выкатывала бы сайт. Пуш в
    чужую ветку и удаление ветки — тоже не повод.
    """
    if event == "ping":
        return False, "ping — вебхук проверяют, выкладывать нечего"
    if event != "push":
        return False, f"событие {event!r} — не push, пропускаю"
    if payload.get("deleted"):
        return False, "это удаление ветки, а не пуш кода"
    ref = payload.get("ref", "")
    wanted = f"refs/heads/{branch}"
    if ref != wanted:
        return False, f"ветка {ref!r} — жду {wanted!r}, пропускаю"
    return True, "выкладываю"


def read_env(path: str) -> dict[str, str]:
    """
    Два ключа Telegram из .env проекта — и ничего больше.

    Сайт уже знает, куда писать владельцу; заводить для слушателя свой
    бот значило бы держать два места, где это записано, и однажды они
    разошлись бы.
    """
    wanted = {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}
    found: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                key, sep, value = line.strip().partition("=")
                if sep and key.strip() in wanted:
                    found[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return found


def tell(text: str) -> None:
    """Сообщение владельцу. Молча, если Telegram не настроен или недоступен."""
    env = read_env(os.path.join(ROOT, ".env"))
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10)
    except Exception as error:  # сообщение — не повод ронять слушателя
        print(f"Telegram не ответил: {error}")


def summary(head: str, after: str) -> str:
    """Первая строка коммита — чтобы в сообщении было видно, что именно выехало."""
    return (head or "").strip().splitlines()[0][:120] if head else after[:7]


def watch(process: subprocess.Popen, title: str) -> None:
    """Дождаться выкладки и сказать, чем кончилось."""
    code = process.wait()
    if code == 0:
        print(f"Выложено: {title}")
        tell(f"ПОРЯДОК // АВТОДЕПЛОЙ\nВыложено: {title}")
    else:
        print(f"Выкладка упала с кодом {code}")
        tell(
            "ПОРЯДОК // АВТОДЕПЛОЙ УПАЛ\n"
            f"Коммит: {title}\n"
            f"deploy.sh вернул {code}. Сайт остался на прежней версии.\n"
            "Журнал: journalctl -u poryadok-deploy-webhook -n 200"
        )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def do_POST(self) -> None:  # noqa: N802 — имя задано BaseHTTPRequestHandler
        global _running

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""

        if not SECRET:
            self._respond(503, "DEPLOY_WEBHOOK_SECRET не задан")
            return
        signature = self.headers.get("X-Hub-Signature-256")
        if not verify_signature(SECRET, body, signature):
            # В журнал — то, по чему видна причина, но не сам секрет:
            # нет заголовка — в GitHub не задан Secret; есть, а не
            # сходится — вставлено не то значение (частая ошибка: вся
            # строка «DEPLOY_WEBHOOK_SECRET=…» вместе с префиксом).
            print(f"401: заголовок подписи {'есть' if signature else 'НЕТ'}, "
                  f"тело {len(body)} байт, секрет на сервере {len(SECRET)} символов")
            self._respond(401, "подпись не совпала")
            return

        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            self._respond(400, "тело не JSON")
            return

        deploy, reason = should_deploy(self.headers.get("X-GitHub-Event"), payload)
        print(reason)
        if not deploy:
            self._respond(200, reason)
            return

        with _lock:
            if _running is not None and _running.poll() is None:
                # В очередь не ставим: следующий пуш пришлёт свой вебхук,
                # а deploy.sh забирает ветку целиком — то, что накопилось
                # к тому моменту, уедет одним разом.
                self._respond(202, "выкладка уже идёт, этот пуш заберёт следующая")
                return
            commit = payload.get("head_commit") or {}
            title = summary(commit.get("message", ""), payload.get("after", ""))
            _running = subprocess.Popen(["bash", DEPLOY_SCRIPT, BRANCH], cwd=ROOT)
            threading.Thread(target=watch, args=(_running, title), daemon=True).start()
        self._respond(202, "выкладываю в фоне")

    def _respond(self, code: int, message: str) -> None:
        body = json.dumps({"message": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    if not SECRET:
        sys.exit("DEPLOY_WEBHOOK_SECRET не задан — слушатель отказывается стартовать")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Слушаю вебхук выкладки на 127.0.0.1:{PORT}, ветка {BRANCH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
