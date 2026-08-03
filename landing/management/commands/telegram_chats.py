"""Показывает id чатов и каналов, где работает бот.

Нужен, чтобы заполнить TELEGRAM_CHAT_ID и TELEGRAM_CLUB_CHAT_ID, не
угадывая. Telegram не даёт списка чатов напрямую, поэтому смотрим
последние события: любое сообщение в канале или чате приносит его id.

    python manage.py telegram_chats

Если список пуст — напишите что-нибудь в канал и в личку боту, потом
запустите ещё раз. События хранятся сутки.

ВАЖНО: команда забирает события и тем самым их отмечает прочитанными.
Пока сайт не использует режим webhook, это безопасно.
"""
import requests
from django.conf import settings
from django.core.management.base import BaseCommand

API = 'https://api.telegram.org/bot{token}/{method}'


class Command(BaseCommand):
    help = 'Показывает id чатов и каналов, доступных боту'

    def handle(self, *args, **options):
        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
        if not token:
            self.stdout.write(self.style.ERROR(
                'TELEGRAM_BOT_TOKEN пуст. Заполните .env и повторите.'))
            return

        try:
            r = requests.get(API.format(token=token, method='getUpdates'),
                             params={'limit': 100}, timeout=15)
            data = r.json()
        except requests.exceptions.RequestException as e:
            self.stdout.write(self.style.ERROR(f'Сеть недоступна: {e}'))
            return

        if not data.get('ok'):
            self.stdout.write(self.style.ERROR(
                f'Telegram отказал: {data.get("description")}'))
            return

        found = {}
        for update in data.get('result', []):
            for key in ('message', 'channel_post', 'edited_channel_post',
                        'my_chat_member', 'chat_member'):
                chat = (update.get(key) or {}).get('chat')
                if chat:
                    found[chat['id']] = chat

        if not found:
            self.stdout.write(
                'Событий нет.\n'
                'Напишите что-нибудь боту в личку и опубликуйте любой пост\n'
                'в канале «Клуб Порядок», затем запустите команду снова.\n'
                'Если бот только что добавлен в канал — опубликуйте пост:\n'
                'без единого поста Telegram не присылает id канала.')
            return

        self.stdout.write('\nНайденные чаты:\n')
        for chat_id, chat in found.items():
            kind = {
                'private': 'личка',
                'group': 'группа',
                'supergroup': 'группа',
                'channel': 'канал',
            }.get(chat.get('type'), chat.get('type'))
            name = chat.get('title') or chat.get('username') or chat.get('first_name') or '—'
            self.stdout.write(f'  {chat_id:>16}   {kind:<8} {name}')

        self.stdout.write('''
Что куда вписать в /var/www/s-poryadok/.env:

  TELEGRAM_CHAT_ID       — ваша личка с ботом (тип «личка», id без минуса)
  TELEGRAM_CLUB_CHAT_ID  — канал «Клуб Порядок» (тип «канал», id с минусом)

После правки: sudo systemctl restart gunicorn
Проверить: python manage.py check_telegram --send
''')
