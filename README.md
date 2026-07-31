# Системы Порядок

Сайт и CRM для сборки операционных систем малому бизнесу.
Python 3.11+, Django, SQLite на разработке.

Что внутри:

- **Лендинг** — одностраничник с одной задачей: заявка на бесплатный разбор.
- **CRM** — заявки, клиенты, проекты, оплаты. Живёт в админке Django по адресу `/admin/`.
- **Клуб** — страница закрытого телеграм-канала с подпиской и выдачей доступа.
- **Экспресс-проверка** — быстрый расчёт по выгрузке продаж в `.xlsx` или `.csv`.

---

## Запуск на Windows 10 в PyCharm Professional

Ниже — по шагам, с командами для PowerShell. Всё выполняется в терминале,
встроенном в PyCharm (`Alt+F12`).

### 1. Забрать проект

**Если проекта на компьютере ещё нет.** В PyCharm: `File → Project from Version Control`,
вставьте адрес репозитория, выберите папку и нажмите `Clone`.

Или в PowerShell:

```powershell
cd $HOME\PycharmProjects
git clone https://github.com/AntonSergeevich/poryadok-landing.git
cd poryadok-landing
```

**Если проект уже открыт**, заберите свежую ветку:

```powershell
git fetch origin
git checkout claude/business-os-website-design-7hvb9a
git pull origin claude/business-os-website-design-7hvb9a
```

### 2. Виртуальное окружение

PyCharm обычно предлагает создать его сам при открытии проекта — соглашайтесь
(`Python 3.11`, тип `Virtualenv`, папка `venv` внутри проекта).

Вручную из PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Если PowerShell ругается на запрет выполнения скриптов, разрешите их для текущего
пользователя — это делается один раз:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

После активации в начале строки появится `(venv)`.

### 3. Зависимости

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Настройки

```powershell
Copy-Item .env.example .env
```

Откройте `.env` в PyCharm и заполните. Минимум для локального запуска —
`SECRET_KEY` и `DEBUG=True`; без токенов Telegram сайт работает, заявки просто
сохраняются в базу без уведомлений.

Сгенерировать `SECRET_KEY`:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 5. База и администратор

```powershell
python manage.py migrate
python manage.py createsuperuser
```

### 6. Запуск

```powershell
python manage.py runserver
```

Сайт — <http://127.0.0.1:8000/>, CRM — <http://127.0.0.1:8000/admin/>.

**Кнопка «Run» в PyCharm.** Чтобы запускать не из терминала:
`Run → Edit Configurations → + → Django Server`. Если пункта `Django Server` нет,
включите поддержку фреймворка: `File → Settings → Languages & Frameworks → Django`,
галочка `Enable Django Support`, `Django project root` — корень проекта,
`Settings` — `core/settings.py`, `Manage script` — `manage.py`.

### 7. Полезные команды

```powershell
python manage.py makemigrations        # после изменения моделей
python manage.py migrate               # применить изменения к базе
python manage.py expire_club --dry-run # посмотреть, у кого истекла подписка
python manage.py expire_club           # закрыть доступ истёкшим
python manage.py collectstatic         # собрать статику перед выкладкой
```

---

## Как это устроено

```
core/            настройки Django
landing/
  models.py           Lead, Client, Project, Payment, ClubSubscription
  admin.py            CRM: списки, фильтры, массовые действия
  forms.py            проверка ввода на сервере
  views.py            страницы и обработка форм
  services/
    telegram.py       уведомления и доступ в закрытый канал
    payments.py       ЮKassa
  management/commands/
    expire_club.py    закрывает доступ по истечении подписки
  templates/landing/  шаблоны страниц
  static/landing/     свой CSS, JS и шрифты — внешних CDN нет
```

Главное правило обработки заявки: **сначала сохранить в базу, потом отправлять
уведомление**. Если Telegram недоступен, заявка всё равно останется в CRM,
а сбой доставки попадёт в `logs/app.log` и будет виден в списке заявок
колонкой «в TG».

---

## Подключение Telegram

1. Создайте бота у [@BotFather](https://t.me/BotFather), получите токен →
   `TELEGRAM_BOT_TOKEN`.
2. Напишите боту любое сообщение, затем узнайте свой ID у
   [@userinfobot](https://t.me/userinfobot) → `TELEGRAM_CHAT_ID`.
3. Для закрытого клуба: создайте приватный канал, добавьте бота администратором
   с правом «Приглашать по ссылке». ID канала → `TELEGRAM_CLUB_CHAT_ID`
   (обычно вида `-1001234567890`).

Проверить, что всё связалось:

```powershell
python manage.py shell -c "from landing.services import telegram; print(telegram.notify('Проверка связи'))"
```

---

## Подключение оплат

Пока `YOOKASSA_SHOP_ID` и `YOOKASSA_SECRET_KEY` пустые, страница клуба просто
собирает заявки, а оплату вы проводите вручную и отмечаете в CRM. Переписывать
ничего не придётся: как только ключи появятся в `.env`, кнопка сама начнёт вести
на оплату.

Для подключения ЮKassa нужны ИП или самозанятость и договор с банком —
это занимает несколько дней и делается без участия кода.

Адрес для уведомлений, который надо указать в личном кабинете ЮKassa:

```
https://ваш-домен/pay/yookassa/webhook/
```

Обработчик не верит телу уведомления на слово: он берёт оттуда только номер
платежа и переспрашивает статус у API. Подделать оплату, зная адрес обработчика,
не получится.

---

## Автопродление и закрытие доступа в клуб

Доступ закрывается не сам по себе — нужен ежедневный запуск команды.
На сервере это делает cron:

```
0 4 * * * cd /путь/к/проекту && ./venv/bin/python manage.py expire_club >> logs/cron.log 2>&1
```

---

## Выкладка на сервер

Коротко, что должно отличаться от разработки:

- `DEBUG=False` в `.env`, `SECRET_KEY` — новый и длинный;
- `python manage.py collectstatic`, раздача `staticfiles/` через nginx;
- запуск через gunicorn, а не через `runserver`;
- сертификат HTTPS — при `DEBUG=False` включается редирект на https,
  защищённые cookie и HSTS;
- для боевой нагрузки лучше PostgreSQL вместо SQLite, но на текущих объёмах
  SQLite справляется.

---

## Что осталось сделать руками

- [ ] Положить фото в `landing/static/landing/img/anton.jpg` (портрет 4:5).
- [ ] Заменить кейсы на «Листе 04» реальными формулировками и сроками.
- [ ] Проверить и дополнить политику конфиденциальности: ИНН, статус, реквизиты.
- [ ] Завести бота и закрытый канал, заполнить `.env`.
- [ ] Решить, когда подключать эквайринг.
