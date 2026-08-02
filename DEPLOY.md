# Выкладка на боевой сервер

Как устроен прод и что делать при каждом обновлении.

## Что где находится

| | |
|---|---|
| Сервер | Ubuntu VPS, `159.194.230.39` |
| Папка проекта | `/var/www/s-poryadok` |
| Приложение | Gunicorn через сокет `/run/gunicorn.sock`, служба `gunicorn` |
| Веб-сервер | Nginx: SSL, статика, прокси на сокет |
| Сертификат | Let's Encrypt (certbot), `/etc/letsencrypt/live/s-poryadok.ru/` |
| База | SQLite, `/var/www/s-poryadok/db.sqlite3` |
| Боевая ветка | `main` |

Локальная разработка идёт в отдельных ветках, но на сервер уезжает только `main`.

---

## 1. Локально: слить работу в `main`

```powershell
# зафиксировать в своей ветке
git add .
git commit -m "Описание изменений"
git push origin <имя-ветки>

# влить в main
git checkout main
git pull origin main
git merge <имя-ветки>
git push origin main
```

**Если истории разошлись** и merge отказывается работать:

```powershell
git pull origin main --allow-unrelated-histories
```

Дальше разрешить конфликты в PyCharm (`Git → Resolve Conflicts`) и закоммитить.

---

## 2. На сервере: обновить

```bash
ssh root@159.194.230.39
cd /var/www/s-poryadok

git pull origin main
source venv/bin/activate

pip install -r requirements.txt      # только если менялись зависимости
python manage.py migrate
python manage.py collectstatic --noinput

sudo systemctl restart gunicorn
sudo systemctl reload nginx
```

Перед миграциями, которые меняют структуру таблиц, стоит скопировать базу:

```bash
cp db.sqlite3 db.sqlite3.backup-$(date +%F)
```

---

## 3. Проверить после выкладки

```bash
sudo systemctl status gunicorn      # должно быть active (running)
tail -n 50 /var/www/s-poryadok/logs/app.log
sudo tail -n 50 /var/log/nginx/error.log
```

И в браузере: главная, `/club/`, `/privacy/`, `/admin/`,
плюс `/static/landing/css/site.css` — должен отдавать текст, а не 404.

---

## 4. Конфигурация Nginx

`/etc/nginx/sites-available/default` — рабочая версия:

```nginx
# HTTPS: Django + SSL
server {
    listen 443 ssl;
    listen [::]:443 ssl ipv6only=on;

    server_name s-poryadok.ru www.s-poryadok.ru;

    ssl_certificate     /etc/letsencrypt/live/s-poryadok.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/s-poryadok.ru/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    location /static/ {
        alias /var/www/s-poryadok/staticfiles/;
    }

    location /media/ {
        alias /var/www/s-poryadok/media/;
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/run/gunicorn.sock;
    }
}

# HTTP -> HTTPS
server {
    listen 80;
    listen [::]:80;

    server_name s-poryadok.ru www.s-poryadok.ru;

    if ($host = www.s-poryadok.ru) { return 301 https://$host$request_uri; }
    if ($host = s-poryadok.ru)     { return 301 https://$host$request_uri; }

    return 404;
}
```

Проверить конфиг перед перезагрузкой: `sudo nginx -t`.

---

## 5. Настройки Django на проде

В `/var/www/s-poryadok/.env` должно быть как минимум:

```
DEBUG=False
SECRET_KEY=длинная-случайная-строка
```

**Про `SECURE_SSL_REDIRECT`.** Редирект с http на https уже делает Nginx, так что
включать его ещё и в Django не нужно — оставьте `False`. Django всё равно понимает,
что соединение защищённое: `proxy_params` передаёт заголовок `X-Forwarded-Proto`,
а `SECURE_PROXY_SSL_HEADER` в настройках его читает. От этого зависят абсолютные
адреса в OG-тегах и в `canonical`, поэтому `DEBUG=False` на проде обязателен.

**Про HSTS.** По умолчанию выключен (`SECURE_HSTS_SECONDS=0`). Включать —
осознанно и последним: браузеры посетителей запомнят настройку на год,
и откатить её будет практически невозможно.

---

## 6. Что ещё стоит завести на сервере

### Закрытие доступа в клуб по истечении подписки

Само не произойдёт — нужен ежедневный запуск:

```bash
crontab -e
```

```
0 4 * * * cd /var/www/s-poryadok && /var/www/s-poryadok/venv/bin/python manage.py expire_club >> /var/www/s-poryadok/logs/cron.log 2>&1
```

Посмотреть, кого затронет, ничего не меняя:

```bash
cd /var/www/s-poryadok && source venv/bin/activate
python manage.py expire_club --dry-run
```

### Резервная копия базы

В `db.sqlite3` лежат все заявки и клиенты. Если файл пропадёт, восстанавливать
будет нечего. Ежедневная копия с хранением за две недели:

```
30 3 * * * cd /var/www/s-poryadok && cp db.sqlite3 backups/db-$(date +\%F).sqlite3 && find backups/ -name 'db-*.sqlite3' -mtime +14 -delete
```

Папку нужно создать заранее: `mkdir -p /var/www/s-poryadok/backups`.

---

## Сайт перестаёт отвечать: как искать причину

Если сайт ложится и помогает только перезагрузка VPS, диагноз ставится
не по догадкам, а по двум вещам: что показывают логи и работает ли ssh
в момент поломки.

### Первое, что надо выяснить

**Работает ли `ssh root@159.194.230.39`, пока сайт не отвечает?**
Этот один факт делит поиск пополам:

- **ssh работает** — значит сервер жив, проблема в nginx или gunicorn.
  Смотреть `systemctl status gunicorn`, `journalctl -u gunicorn -n 50`,
  `/var/log/nginx/error.log`.
- **ssh тоже не отвечает** — проблема ниже уровня приложения: сеть,
  ядро, ограничения хостинга. Код сайта тут ни при чём.

### Логи после перезагрузки

`dmesg` очищается при ребуте и после него ничего не покажет. Журнал
предыдущей загрузки смотрится так:

```bash
journalctl --list-boots                  # список загрузок
sudo journalctl -k -b -1 | grep -i -E 'oom|killed process|blocked'
sudo journalctl -u gunicorn -b -1 | tail -50
sudo journalctl -u nginx -b -1 | tail -30
```

Если журнал не сохраняется между загрузками, включить:

```bash
sudo mkdir -p /var/log/journal && sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
```

### Наблюдение за состоянием

В репозитории есть `scripts/watch.sh` — раз в 30 секунд пишет в
`logs/watch.log` память, swap, load average, место на диске, число
процессов gunicorn, количество соединений и то, отвечает ли сайт
через сокет и через nginx. Файл остаётся на диске после перезагрузки,
поэтому по нему видно, что происходило в минуты перед падением.

```bash
cd /var/www/s-poryadok
nohup ./scripts/watch.sh >/dev/null 2>&1 &
```

Посмотреть последние записи:

```bash
tail -50 /var/www/s-poryadok/logs/watch.log
```

Остановить: `pkill -f scripts/watch.sh`

### Что ещё проверить

```bash
df -h                    # закончившееся место ломает всё разом
sudo nginx -t            # ошибка в конфиге
ss -s                    # сколько открытых соединений
uptime                   # перезагружался ли сервер сам
```

**IPv6.** Если у домена есть AAAA-запись, а IPv6 на сервере настроен
наполовину, телефон в мобильной сети пойдёт по IPv6 и повиснет, а тот
же телефон по Wi-Fi через IPv4 откроет сайт нормально. Проверить:

```bash
nslookup -type=AAAA s-poryadok.ru
curl -6 -sS -o /dev/null -w '%{http_code}\n' https://s-poryadok.ru/   # с сервера
```

Если AAAA-запись есть, а запрос по IPv6 не проходит — либо чинить IPv6,
либо убрать AAAA-запись у регистратора домена.

---

## Память

На проверенном сервере (2 ГБ, два воркера) памяти хватает с запасом, и
падений из-за неё пока не зафиксировано. Раздел оставлен на будущее:
запас невелик, и при росте трафика память станет узким местом первой.

**Что было.** `pandas` импортировался на уровне модуля в `landing/views.py`,
а URL-конфиг тянет views целиком. Из-за этого pandas вместе с numpy жил
в памяти каждого воркера — даже когда человек просто открывал главную.
Замер: **99 МБ на воркер против 55 МБ без него.** При четырёх воркерах это
лишние 176 МБ на пустом месте.

**Что сделано.** Импорт перенесён внутрь `express_audit` — единственного
места, где pandas нужен. Обычные страницы за него больше не платят.

### Проверить, было ли переполнение памяти

```bash
free -h                                  # сколько свободно сейчас
sudo dmesg -T | grep -i -E 'oom|killed process' | tail -20
sudo journalctl -u gunicorn --since '2 days ago' | grep -i -E 'worker|oom|sigkill' | tail -30
ps -o pid,rss,cmd -C gunicorn --sort=-rss   # сколько ест каждый воркер
```

Если в `dmesg` есть строки вида `Out of memory: Killed process ... gunicorn` —
диагноз подтверждён.

### Сколько держать воркеров

Формула «2 × ядра + 1» рассчитана на серверы с запасом памяти. Для сайта
с небольшим трафиком на маленьком VPS двух-трёх воркеров достаточно,
и лучше иметь запас памяти, чем незанятые воркеры.

Открыть настройку службы:

```bash
sudo systemctl edit --full gunicorn
```

В строке `ExecStart` задать явно:

```
--workers 3 --max-requests 500 --max-requests-jitter 50 --timeout 60
```

`--max-requests` заставляет воркер перезапускаться каждые N запросов —
дешёвая страховка от утечек памяти: даже если что-то течёт, оно не
накапливается сутками. `jitter` разводит перезапуски по времени, чтобы
воркеры не ушли перезагружаться разом.

Применить:

```bash
sudo systemctl daemon-reload
sudo systemctl restart gunicorn
```

### Файл подкачки

Если его нет, любой всплеск памяти сразу убивает процессы. Гигабайт swap
не заменяет память, но превращает падение сервера в кратковременное
замедление:

```bash
free -h | grep -i swap          # если строка пустая или нули — swap нет
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## Если что-то сломалось

| Симптом | Куда смотреть |
|---|---|
| 502 Bad Gateway | `sudo systemctl status gunicorn`, `sudo journalctl -u gunicorn -n 50` |
| Сайт без стилей | не выполнен `collectstatic`, либо путь в `location /static/` не совпадает с `STATIC_ROOT` |
| 500 на всех страницах | `logs/app.log`, `sudo journalctl -u gunicorn -n 50` |
| Заявка не приходит в Telegram | колонка «в TG» в списке заявок; сама заявка при этом сохранена. Проверить `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` в `.env` |
| Изменения не видны | не перезапущен `gunicorn` |
| Бесконечный редирект | включён `SECURE_SSL_REDIRECT=True` при том, что редирект уже делает Nginx |
| Сервер перестал отвечать целиком, помогает только перезагрузка | нехватка памяти — см. раздел «Память» выше |

Откатиться на предыдущую версию:

```bash
cd /var/www/s-poryadok
git log --oneline -5
git checkout <хеш-рабочего-коммита>
sudo systemctl restart gunicorn
```
