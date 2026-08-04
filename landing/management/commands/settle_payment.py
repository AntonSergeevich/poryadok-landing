"""Провести платёж вручную, если уведомление об оплате потерялось.

Уведомление — вещь ненадёжная по своей природе: сервер мог быть занят,
сеть могла оборваться, а GetPlatinum повторных попыток не делает. Деньги
при этом списаны. Значит нужен способ спросить у платёжной системы
напрямую и применить ответ.

    python manage.py settle_payment CLUB-12          посмотреть, ничего не меняя
    python manage.py settle_payment CLUB-12 --apply  и провести, если оплачен
    python manage.py settle_payment --all            проверить все висящие

Правило то же, что в обработчике уведомлений: верим только ответу
/status. Здесь этот ответ вообще единственный источник — никакого
уведомления нет.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from landing.models import Payment
from landing.services import club as club_service
from landing.services import getplatinum as gp


class Command(BaseCommand):
    help = 'Спрашивает статус платежа у GetPlatinum и проводит оплату'

    def add_arguments(self, parser):
        parser.add_argument('deal_id', nargs='?', help='номер заказа, например CLUB-12')
        parser.add_argument('--all', action='store_true',
                            help='проверить все платежи, ожидающие оплаты')
        parser.add_argument('--apply', action='store_true',
                            help='не только показать, но и провести оплату')

    def handle(self, *args, **options):
        if not gp.is_enabled():
            self.stdout.write(self.style.ERROR(
                'GetPlatinum не настроен — проверьте .env'))
            return

        if options['all']:
            payments = list(Payment.objects.filter(
                provider='getplatinum', status=Payment.Status.PENDING
            ).exclude(provider_payment_id=''))
            if not payments:
                self.stdout.write('Висящих платежей нет.')
                return
        elif options['deal_id']:
            payments = list(Payment.objects.filter(
                provider='getplatinum', provider_payment_id=options['deal_id']))
            if not payments:
                self.stdout.write(self.style.ERROR(
                    f'Платёж с номером заказа {options["deal_id"]} не найден.\n'
                    'Посмотреть, какие есть:\n'
                    '  python manage.py settle_payment --all'))
                return
        else:
            self.stdout.write('Укажите номер заказа или --all')
            return

        for payment in payments:
            self._one(payment, options['apply'])

    def _one(self, payment, apply_it):
        deal_id = payment.provider_payment_id
        self.stdout.write(f'\n=== {deal_id} — {payment.amount:.0f} ₽ ===')
        self.stdout.write(f'  у нас: {payment.get_status_display()}')

        status = gp.fetch_status(deal_id)
        if status is None:
            self.stdout.write(self.style.ERROR(
                '  статус получить не удалось — смотрите logs/app.log'))
            return

        paid = gp.is_paid(status)
        self.stdout.write(f'  у GetPlatinum: {"ОПЛАЧЕН" if paid else "не оплачен"}')
        for field in ('amount', 'paidAt', 'paymentSystem', 'mdOrder'):
            if status.get(field) is not None:
                self.stdout.write(f'    {field}: {status[field]}')

        if not paid:
            self.stdout.write('  Проводить нечего.')
            return

        if payment.status == Payment.Status.SUCCEEDED:
            self.stdout.write('  Уже проведён, повторно ничего не делаем.')
            return

        if not apply_it:
            self.stdout.write(self.style.WARNING(
                '  Оплачен, но не проведён. Добавьте --apply, чтобы провести.'))
            return

        with transaction.atomic():
            newly_paid = payment.mark_succeeded()
        if not newly_paid:
            self.stdout.write('  Уже был проведён.')
            return

        subscription = club_service.grant_access(payment)
        self.stdout.write(self.style.SUCCESS('  Проведён.'))
        if subscription:
            self.stdout.write(f'    подписка до: {subscription.ends_at:%d.%m.%Y}')
            self.stdout.write(
                f'    ссылка в канал: {subscription.invite_link or "НЕ ВЫДАНА"}')
