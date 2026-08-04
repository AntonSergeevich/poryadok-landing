"""Напоминает участникам клуба, что доступ скоро кончится.

Запускать раз в сутки, до закрытия доступа:

    python manage.py club_reminders                 за 3 дня (по умолчанию)
    python manage.py club_reminders --days 7        за неделю
    python manage.py club_reminders --dry-run       посмотреть, кого затронет

Человек обычно не отказывается от клуба, а просто забывает. Напоминание
за несколько дней — самое дешёвое, что можно сделать для продлений.

Напоминаем один раз за срок: поле reminded_at хранит отметку и сбрасывается
при следующем продлении. Иначе ежедневный запуск слал бы одно и то же
сообщение каждый день до самого конца подписки.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from landing.models import ClubSubscription
from landing.services import club as club_service


class Command(BaseCommand):
    help = 'Предупреждает участников клуба об окончании доступа'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=3,
                            help='за сколько дней предупреждать (по умолчанию 3)')
        parser.add_argument('--dry-run', action='store_true',
                            help='только показать, ничего не отправляя')

    def handle(self, *args, **options):
        days = options['days']
        dry = options['dry_run']
        now = timezone.now()
        edge = now + timedelta(days=days)

        # Берём тех, у кого срок уже на подходе, но ещё не вышел, и кому
        # за этот срок ещё не писали.
        due = list(ClubSubscription.objects.filter(
            status=ClubSubscription.Status.ACTIVE,
            ends_at__gt=now, ends_at__lte=edge,
        ).filter(
            # Напоминание либо не отправлялось вовсе, либо отправлялось
            # до последнего продления — значит про новый срок человек
            # ещё не знает.
            reminded_at__isnull=True,
        ).select_related('client'))

        stale = list(ClubSubscription.objects.filter(
            status=ClubSubscription.Status.ACTIVE,
            ends_at__gt=now, ends_at__lte=edge,
            reminded_at__isnull=False,
            reminded_at__lt=timezone.now() - timedelta(days=days + 1),
        ).select_related('client'))
        due.extend(s for s in stale if s not in due)

        if not due:
            self.stdout.write(f'Некого предупреждать: ни у кого срок не '
                              f'подходит в ближайшие {days} дн.')
            return

        for subscription in due:
            label = (f'{subscription.client.name} · до '
                     f'{subscription.ends_at:%d.%m.%Y} '
                     f'({subscription.days_left} дн.)')
            if dry:
                self.stdout.write(f'[проверка] напомнил бы: {label}')
                continue

            sent = club_service.remind(subscription)
            note = 'написал участнику' if sent else 'участнику написать не смог'
            self.stdout.write(f'Напомнил: {label} — {note}')

        if not dry:
            self.stdout.write(self.style.SUCCESS(
                f'Готово, напоминаний: {len(due)}'))
