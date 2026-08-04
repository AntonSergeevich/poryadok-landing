"""Подписка бота на сообщения — включить, посмотреть, выключить.

Без этого бот не увидит ни одного сообщения: Telegram просто некуда их
присылать, и кнопка «Открыть бота» окажется пустышкой.

    python manage.py telegram_webhook            что настроено сейчас
    python manage.py telegram_webhook --set      подписаться
    python manage.py telegram_webhook --delete   отписаться

Адрес обработчика собирается из SITE_HOST и секрета TELEGRAM_WEBHOOK_SECRET.
"""
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand

from landing.services import telegram as tg


class Command(BaseCommand):
    help = 'Управляет подпиской бота на сообщения'

    def add_arguments(self, parser):
        parser.add_argument('--set', action='store_true', help='подписаться')
        parser.add_argument('--delete', action='store_true', help='отписаться')

    def handle(self, *args, **options):
        secret = getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '')
        host = getattr(settings, 'SITE_HOST', 's-poryadok.ru')

        if not getattr(settings, 'TELEGRAM_BOT_TOKEN', None):
            self.stdout.write(self.style.ERROR('TELEGRAM_BOT_TOKEN пуст.'))
            return

        if options['delete']:
            result = tg.delete_webhook()
            self.stdout.write('Отписался.' if result is not None
                              else self.style.ERROR('Не вышло, см. logs/app.log'))
            return

        if not secret:
            self.stdout.write(self.style.ERROR(
                'TELEGRAM_WEBHOOK_SECRET пуст. Впишите в .env любую длинную\n'
                'строку без пробелов, например такую:\n'
                f'  TELEGRAM_WEBHOOK_SECRET={secrets.token_urlsafe(24)}\n'
                'затем sudo systemctl restart gunicorn'))
            return

        url = f'https://{host}/tg/{secret}/'

        if options['set']:
            result = tg.set_webhook(url, secret)
            if result is None:
                self.stdout.write(self.style.ERROR('Не вышло, см. logs/app.log'))
                return
            self.stdout.write(self.style.SUCCESS('Подписался. Адрес:'))
            self.stdout.write(f'  {url}')
            self.stdout.write(
                '\nТеперь откройте своего бота в Telegram, нажмите «Запустить»\n'
                'и поделитесь номером. Если бот ответит — цепочка работает.\n')
            return

        info = tg.webhook_info()
        if info is None:
            self.stdout.write(self.style.ERROR('Спросить не вышло, см. logs/app.log'))
            return

        current = info.get('url') or ''
        if not current:
            self.stdout.write('Подписки нет — бот сообщений не получает.')
            self.stdout.write('Включить: python manage.py telegram_webhook --set')
        elif current == url:
            self.stdout.write(self.style.SUCCESS(f'Подписан: {current}'))
        else:
            self.stdout.write(self.style.WARNING(
                f'Подписан на ДРУГОЙ адрес: {current}\n'
                f'Ожидался: {url}\n'
                'Обновить: python manage.py telegram_webhook --set'))

        for field in ('pending_update_count', 'last_error_message', 'last_error_date'):
            if info.get(field):
                self.stdout.write(f'  {field}: {info[field]}')
