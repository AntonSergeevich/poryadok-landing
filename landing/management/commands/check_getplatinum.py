"""Проверка связи с GetPlatinum по шагам.

    python manage.py check_getplatinum          проверить настройки и ключ
    python manage.py check_getplatinum --link   и создать пробную ссылку на оплату

Пробная ссылка создаётся на 10 ₽ с выдуманным номером заказа. Заказ
остаётся неоплаченным и ни на что не влияет — по ссылке можно просто
посмотреть, что платёжная форма открывается и на ней ваше название.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from landing.services import getplatinum as gp


class Command(BaseCommand):
    help = 'Проверяет подключение к GetPlatinum'

    def add_arguments(self, parser):
        parser.add_argument('--link', action='store_true',
                            help='создать пробную ссылку на оплату 10 ₽')
        parser.add_argument('--full', action='store_true',
                            help='боевая проверка: настоящая подписка на 10 ₽')
        parser.add_argument('--site', default='https://s-poryadok.ru',
                            help='адрес сайта для обратных ссылок')

    def ok(self, text):
        self.stdout.write(self.style.SUCCESS(f'  ХОРОШО  {text}'))

    def bad(self, text):
        self.stdout.write(self.style.ERROR(f'  ПРОБЛЕМА  {text}'))

    def info(self, text):
        self.stdout.write(f'          {text}')

    def handle(self, *args, **options):
        key = getattr(settings, 'GETPLATINUM_API_KEY', None)

        self.stdout.write('\n=== 1. Настройки в .env ===')
        if not key:
            self.bad('GETPLATINUM_API_KEY пуст.')
            self.info('Личный кабинет → Настройки → шестерёнка у организации.')
            return
        self.ok(f'ключ задан (…{key[-6:]})')

        base = gp.base_url()
        if not base:
            self.bad('Не задан ни GETPLATINUM_ACCOUNT, ни GETPLATINUM_BASE_URL.')
            self.info('Проще всего указать имя аккаунта — оно видно')
            self.info('в адресной строке личного кабинета GetPlatinum.')
            self.info('Например, для https://poryadok.getplatinum.ru/ это:')
            self.info('  GETPLATINUM_ACCOUNT=poryadok')
            return
        self.ok(f'адрес API: {base}')

        self.stdout.write('\n=== 2. Ключ принят, организация видна ===')
        org = gp._call('get-organization', {})
        if org is None:
            self.bad('Организацию получить не удалось.')
            self.info('Частые причины:')
            self.info('  • заявка на подключение организации ещё не одобрена —')
            self.info('    до одобрения API не работает;')
            self.info('  • ключ скопирован не полностью или от другой организации;')
            self.info('  • в адресе не то имя аккаунта.')
            self.info('Подробности — в logs/app.log.')
            return
        name = org.get('name') or org.get('title') or '—'
        self.ok(f'организация: {name}')
        for field in ('inn', 'status', 'isActive'):
            if field in org:
                self.info(f'{field}: {org[field]}')

        if options['full']:
            self._full(options)
            return

        if not options['link']:
            self.stdout.write(
                '\nДальше:\n'
                '  --link  создать пробную ссылку на оплату 10 ₽ (платить не нужно)\n'
                '  --full  завести настоящую подписку на 10 ₽ и проверить всю цепочку\n')
            return

        self.stdout.write('\n=== 3. Пробная ссылка на оплату ===')
        site = options['site'].rstrip('/')
        deal_id, url = gp.create_payment(
            deal_id='TEST-CHECK-1',
            amount=10,
            title='Проверка связи',
            client_id='TEST-CLIENT',
            notification_url=f'{site}/pay/getplatinum/webhook/',
            success_url=f'{site}/club/done/',
            phone=getattr(settings, 'SITE_PHONE', '') or '+79954412021',
            name=getattr(settings, 'SITE_OWNER', ''),
        )
        if not url:
            self.bad('Ссылку создать не удалось — смотрите logs/app.log.')
            return
        self.ok('ссылка получена, форма оплаты работает:')
        self.info(url)
        self.stdout.write(
            '\nОткройте её в браузере. Должна открыться платёжная форма\n'
            'с вашим названием и суммой 10 ₽. Платить не обязательно —\n'
            'неоплаченный заказ ни на что не влияет.\n'
            '\nЕсли всё же оплатите, придёт уведомление на webhook,\n'
            'и в logs/app.log будет видно, сошлась ли подпись:\n'
            '  grep -i "подпись не совпала" logs/app.log\n')

        self.stdout.write('=== 4. Что видит GetPlatinum об этом заказе ===')
        status = gp.fetch_status(deal_id)
        if status is None:
            self.bad('Статус получить не удалось.')
        else:
            self.ok(f'оплачен: {"да" if gp.is_paid(status) else "нет, как и ожидалось"}')

    def _full(self, options):
        """Боевая проверка всей цепочки за 10 рублей.

        Пробная ссылка из --link проверяет только связь: заказ с выдуманным
        номером в базе не заведён, и уведомление о его оплате наш обработчик
        честно пропустит мимо. А сломаться может как раз то, что дальше:
        нашёлся ли платёж, включилась ли подписка, выдалась ли ссылка в
        канал. Поэтому здесь заводится настоящая подписка — просто на 10 ₽
        вместо тарифной цены.

        Деньги возвращаются вам же, теряется только комиссия.
        """
        from landing.models import Client, ClubSubscription, Payment

        site = options['site'].rstrip('/')
        client, _ = Client.objects.get_or_create(
            phone=getattr(settings, 'SITE_PHONE', '+79954412021'),
            defaults={'name': 'Проверка оплаты', 'note': 'Заведён командой check_getplatinum'})

        subscription = ClubSubscription.objects.create(
            client=client, plan=ClubSubscription.Plan.MONTH, price=10)
        payment = Payment.objects.create(
            client=client, amount=10, purpose=Payment.Purpose.CLUB,
            provider='getplatinum', payer_phone=client.phone)
        subscription.payment = payment
        subscription.save(update_fields=['payment', 'updated_at'])

        deal_id = f'CLUB-{payment.pk}'
        _, url = gp.create_payment(
            deal_id=deal_id,
            amount=10,
            title='Клуб «Порядок», проверка',
            client_id=f'CLIENT-{client.pk}',
            notification_url=f'{site}/pay/getplatinum/webhook/',
            success_url=f'{site}/club/done/',
            phone=client.phone,
            name=client.name,
            custom={'payment_pk': str(payment.pk)},
        )
        if not url:
            self.bad('Ссылку создать не удалось — смотрите logs/app.log.')
            subscription.delete()
            payment.delete()
            return

        self.stdout.write('\n=== 3. Боевая проверка цепочки ===')
        self.ok(f'заказ {deal_id} заведён, подписка №{subscription.pk} ждёт оплаты')
        self.info(url)
        self.stdout.write(f'''
Оплатите эту ссылку на 10 ₽ — деньги придут вам же, потеряется только
комиссия. После оплаты проверьте, что сработала вся цепочка:

  python manage.py shell -c "from landing.models import ClubSubscription as C; \\
    s=C.objects.get(pk={subscription.pk}); \\
    print('статус:', s.get_status_display()); \\
    print('до:', s.ends_at); \\
    print('ссылка в канал:', s.invite_link or 'НЕ ВЫДАНА')"

Должно быть: статус «Активна», дата через месяц, ссылка выдана.

И отдельно — сошлась ли подпись уведомления:

  grep -i "подпись не совпала" logs/app.log

Пусто — можно включать GETPLATINUM_STRICT_CHECKSUM=True в .env.

Убрать проверочную запись потом:

  python manage.py shell -c "from landing.models import ClubSubscription as C; \\
    C.objects.filter(pk={subscription.pk}).delete()"
''')
