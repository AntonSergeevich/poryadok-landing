"""Напомнить о заявках, по которым вышел срок.

Запускать раз в час:

    python manage.py lead_reminders
    python manage.py lead_reminders --dry-run    посмотреть, кого затронет

Заявка в состоянии «думает» сама из него не выйдет. Если о ней
не напомнить, она тихо умрёт через месяц — и это самая дешёвая из всех
потерь, потому что разговор уже состоялся.

Отметка reminded_at ставится после отправки: без неё ежечасный запуск
слал бы одно и то же сообщение до конца времён.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from landing.models import Lead
from landing.services import notify


class Command(BaseCommand):
    help = 'Напоминает о заявках, по которым подошёл срок'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='только показать, ничего не отправляя')

    def handle(self, *args, **options):
        due = list(Lead.objects.filter(
            remind_at__lte=timezone.now(),
            reminded_at__isnull=True,
        ).exclude(status__in=(Lead.Status.WON, Lead.Status.LOST)))

        if not due:
            self.stdout.write('Напоминать не о чем.')
            return

        for lead in due:
            label = f'{lead.name or "без имени"} · {lead.phone_pretty}'
            if options['dry_run']:
                self.stdout.write(f'[проверка] напомнил бы: {label}')
                continue

            sent = notify.lead_reminder(lead)
            # Отметку ставим в любом случае. Иначе неотправленное
            # напоминание будет повторяться каждый час до бесконечности,
            # и в журнале утонет всё остальное.
            lead.reminded_at = timezone.now()
            lead.save(update_fields=['reminded_at', 'updated_at'])
            self.stdout.write(
                f'{"Напомнил" if sent else "Не смог отправить"}: {label}')

        if not options['dry_run']:
            self.stdout.write(self.style.SUCCESS(f'Готово, заявок: {len(due)}'))
