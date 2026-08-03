"""Почему заявки не приходят в Telegram.

Проверяет по шагам всю цепочку и говорит, на каком именно шаге обрыв.
Запускать на сервере:

    cd /var/www/s-poryadok
    source venv/bin/activate
    python manage.py check_telegram

Можно сразу отправить проверочное сообщение:

    python manage.py check_telegram --send
"""
import socket

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from landing.models import Lead

API = 'https://api.telegram.org/bot{token}/{method}'


class Command(BaseCommand):
    help = 'Проверяет доставку заявок в Telegram по шагам'

    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true',
                            help='отправить проверочное сообщение в рабочий чат')

    def ok(self, text):
        self.stdout.write(self.style.SUCCESS(f'  ХОРОШО  {text}'))

    def bad(self, text):
        self.stdout.write(self.style.ERROR(f'  ПРОБЛЕМА  {text}'))

    def info(self, text):
        self.stdout.write(f'          {text}')

    def handle(self, *args, **options):
        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
        chat = getattr(settings, 'TELEGRAM_CHAT_ID', None)

        self.stdout.write('\n=== 1. Настройки в .env ===')
        if not token:
            self.bad('TELEGRAM_BOT_TOKEN пуст.')
            self.info('Взять у @BotFather, вписать в /var/www/s-poryadok/.env,')
            self.info('затем: sudo systemctl restart gunicorn')
            return
        self.ok(f'TELEGRAM_BOT_TOKEN задан (…{token[-6:]})')

        if not chat:
            self.bad('TELEGRAM_CHAT_ID пуст — отправлять некуда.')
            self.info('Свой id можно узнать у @userinfobot в Telegram.')
        else:
            self.ok(f'TELEGRAM_CHAT_ID = {chat}')

        self.stdout.write('\n=== 2. Сеть до api.telegram.org ===')
        try:
            addr = socket.gethostbyname('api.telegram.org')
            self.ok(f'имя разрешается в адрес {addr}')
        except OSError as e:
            self.bad(f'имя не разрешается: {e}')
            self.info('Проблема с DNS на сервере.')
            return

        try:
            with socket.create_connection((addr, 443), timeout=7):
                self.ok('порт 443 отвечает')
        except OSError as e:
            self.bad(f'соединение не устанавливается: {e}')
            self.info('Сервер не может достучаться до Telegram. Обычно это')
            self.info('блокировка исходящих соединений. Решения — в DEPLOY.md,')
            self.info('раздел «Заявки не приходят в Telegram».')
            return

        self.stdout.write('\n=== 3. Бот отвечает ===')
        try:
            r = requests.get(API.format(token=token, method='getMe'), timeout=10)
            data = r.json()
        except requests.exceptions.RequestException as e:
            self.bad(f'запрос не прошёл: {e}')
            self.info('Соединение открывается, но обмен не идёт — похоже на')
            self.info('фильтрацию трафика. См. DEPLOY.md.')
            return
        except ValueError:
            self.bad(f'ответ не похож на ответ Telegram (HTTP {r.status_code})')
            self.info(f'Начало ответа: {r.text[:120]}')
            return

        if not data.get('ok'):
            self.bad(f'Telegram отказал: {data.get("description")}')
            self.info('Обычно это неверный токен. Проверить у @BotFather.')
            return
        bot = data['result']
        self.ok(f'бот @{bot.get("username")} на связи')

        if not chat:
            self.stdout.write('\nДальше проверять нечего: не задан TELEGRAM_CHAT_ID.')
            return

        self.stdout.write('\n=== 4. Доступ в чат ===')
        r = requests.get(API.format(token=token, method='getChat'),
                         params={'chat_id': chat}, timeout=10)
        data = r.json()
        if not data.get('ok'):
            self.bad(f'чат недоступен: {data.get("description")}')
            self.info('Частые причины:')
            self.info('  • вы не начали диалог с ботом — откройте его в Telegram')
            self.info('    и нажмите «Запустить»;')
            self.info('  • для группы: бот не добавлен в неё;')
            self.info('  • id группы должен начинаться с минуса, например -1001234567890.')
            return
        title = data['result'].get('title') or data['result'].get('username') or chat
        self.ok(f'чат доступен: {title}')

        self.stdout.write('\n=== 5. Заявки в базе ===')
        total = Lead.objects.count()
        undelivered = Lead.objects.filter(delivered_to_telegram=False).count()
        self.info(f'всего заявок: {total}, из них не ушло в Telegram: {undelivered}')
        if total and undelivered == total:
            self.info('Ни одна не ушла — значит связь не работала с самого начала.')

        if options['send']:
            self.stdout.write('\n=== 6. Проверочное сообщение ===')
            r = requests.post(API.format(token=token, method='sendMessage'),
                              data={'chat_id': chat,
                                    'text': 'ПОРЯДОК // проверка связи. '
                                            'Если вы это видите, заявки будут доходить.'},
                              timeout=10)
            data = r.json()
            if data.get('ok'):
                self.ok('сообщение доставлено — цепочка работает целиком')
            else:
                self.bad(f'не отправилось: {data.get("description")}')
        else:
            self.stdout.write('\nЧтобы отправить проверочное сообщение, добавьте --send')
