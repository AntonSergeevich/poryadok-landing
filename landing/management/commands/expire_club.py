"""Закрывает доступ в клуб тем, у кого подписка кончилась.

Запускать раз в сутки:
    python manage.py expire_club

На сервере — через cron или systemd timer. На Windows во время разработки
можно просто вызывать руками: команда идемпотентна.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from landing.models import ClubSubscription
from landing.services import telegram as tg


class Command(BaseCommand):
    help = 'Переводит истёкшие подписки в статус «Истекла» и убирает их из канала.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Только показать, кого затронет, ничего не менять.')

    def handle(self, *args, **options):
        dry = options['dry_run']
        now = timezone.now()
        # Список, а не queryset: после смены статусов повторный запрос вернёт пусто.
        expired = list(ClubSubscription.objects.filter(
            status=ClubSubscription.Status.ACTIVE, ends_at__lte=now
        ).select_related('client'))

        if not expired:
            self.stdout.write('Истёкших подписок нет.')
            return

        for subscription in expired:
            client = subscription.client
            label = f'{client.name} (@{client.telegram_username or "без ника"})'
            if dry:
                self.stdout.write(f'[проверка] закрыл бы доступ: {label}')
                continue

            subscription.status = ClubSubscription.Status.EXPIRED
            subscription.save(update_fields=['status', 'updated_at'])

            if client.telegram_user_id:
                removed = tg.remove_from_club(client.telegram_user_id)
                note = 'исключён из канала' if removed else 'из канала убрать не вышло'
            else:
                note = 'telegram ID неизвестен — уберите из канала вручную'
            self.stdout.write(f'Доступ закрыт: {label} — {note}')

        if not dry:
            tg.notify(f'ПОРЯДОК // КЛУБ\nЗакрыт доступ по истечении: {len(expired)}')
        self.stdout.write(self.style.SUCCESS('Готово.'))
