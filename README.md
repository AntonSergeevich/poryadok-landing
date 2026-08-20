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

**Если проект уже открыт**, заберите свежую версию:

```powershell
git checkout main
git pull origin main
```

Работа ведётся в отдельных ветках, но на сервер уезжает только `main` —
порядок слияния описан в [DEPLOY.md](DEPLOY.md).

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
  models.py           Lead, Client, Project, Stage, StageTask, Payment,
                      ClubSubscription, Survey
  admin.py            CRM: списки, фильтры, массовые действия
  forms.py            проверка ввода на сервере
  views.py            страницы сайта и обработка форм
  cabinet.py          кабинет исполнителя и кабинет заказчика
  survey.py           вопросы разбора, подсчёт, выводы — одними данными
  constructor.py      блоки системы, цены, расчёт — одними данными
  works.py            портфолио: что было и что стало
  services/
    access.py         выдача заказчику логина и пароля
    telegram.py       уведомления и доступ в закрытый канал
    getplatinum.py    приём оплат
    club.py           жизненный цикл подписки
  management/commands/
    expire_club.py    закрывает доступ по истечении подписки
  templates/landing/  шаблоны страниц, cabinet/ — кабинета
  static/landing/     свой CSS, JS и шрифты — внешних CDN нет
```

**Три файла с данными вместо трёх экранов настроек.** Вопросы разбора,
блоки конструктора и работы в портфолио описаны словарями в `survey.py`,
`constructor.py` и `works.py`. Поменять цену, добавить работу или
переформулировать вопрос — это правка одной строки, а не поход в вёрстку.

**Кабинет: две двери на один адрес.** `/cabinet/` разводит по роли внутри,
а не двумя адресами: ссылку отправляют в мессенджер, и открываться она
обязана и у исполнителя, и у заказчика.

Переключение этапа на шкале ничего не спрашивает у сервера — карточки
приезжают вместе со страницей. Действия (отметить задачу, сменить статус)
уходят запросом и возвращают перерисованные карточку и шкалу, собранные
тем же шаблоном. Без JavaScript всё это работает обычными формами.

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

```bash
python manage.py shell -c "from landing.services import telegram; print(telegram.notify('Проверка связи'))"
```

Выведет `True`, если сообщение дошло.

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
https://s-poryadok.ru/pay/yookassa/webhook/
```

Обработчик не верит телу уведомления на слово: он берёт оттуда только номер
платежа и переспрашивает статус у API. Подделать оплату, зная адрес обработчика,
не получится.

---

## Автопродление и закрытие доступа в клуб

Доступ закрывается не сам по себе — нужен ежедневный запуск команды.
На сервере это делает cron:

```
0 4 * * * cd /var/www/s-poryadok && /var/www/s-poryadok/venv/bin/python manage.py expire_club >> /var/www/s-poryadok/logs/cron.log 2>&1
```

---

## Выкладка на сервер

Сайт живёт на `s-poryadok.ru`: Ubuntu VPS, Gunicorn через сокет, Nginx для SSL
и статики, боевая ветка — `main`.

Порядок обновления, конфигурация Nginx, настройки прода и разбор типовых
поломок — в **[DEPLOY.md](DEPLOY.md)**.

Коротко, что делается на сервере при каждом обновлении:

```bash
cd /var/www/s-poryadok
git pull origin main
source venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart gunicorn
```

---

## Что осталось сделать руками

- [x] Положить фото в `landing/static/landing/img/avatar.JPG` (портрет 4:5).
- [x] Выложить на боевой сервер.
- [x] Заменить кейсы на «Листе 04» реальными работами (da-des.ru, linguich.ru).
- [ ] Проставить сроки работ в `landing/works.py` — поле `term`. Пока пусто,
      строка со сроком просто не показывается: лучше промолчать, чем
      поставить круглое число, которое придётся защищать в разговоре.
- [ ] Проверить цены блоков в `landing/constructor.py` — там мои прикидки.
- [ ] Проверить и дополнить политику конфиденциальности: ИНН, статус, реквизиты.
- [ ] Завести бота и закрытый канал, заполнить `.env` на сервере.
- [ ] Поставить в cron `expire_club` и резервное копирование базы — см. DEPLOY.md.
- [ ] Решить, когда подключать эквайринг.
