"""Проверки, которые ловят поломки до того, как их увидят клиенты.

Запуск: python manage.py test landing
"""
import re
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from . import survey as survey_logic
from .forms import LeadForm
from .models import (Client, ClubSubscription, Lead, Payment, Survey,
                     format_phone, normalize_phone)
import json
from decimal import Decimal
from io import StringIO

from django.core.management import call_command

from .services import analysis
from .services import getplatinum as gp
from .services import telegram as tg_service

TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates' / 'landing'


class TemplateCommentTests(TestCase):
    """Django вырезает {# ... #} только в пределах одной строки.

    Многострочный комментарий молча попадает на страницу как текст. Один раз
    он добавил странице 300px горизонтальной прокрутки, второй — сдвинул
    портрет на 128px вниз. Оба раза это заметили только глазами.
    """

    def test_no_multiline_short_comments(self):
        for path in sorted(TEMPLATES_DIR.glob('*.html')):
            source = path.read_text(encoding='utf-8')
            for match in re.finditer(r'\{#', source):
                closing = source.find('#}', match.start())
                line = source.count('\n', 0, match.start()) + 1
                self.assertNotEqual(
                    closing, -1,
                    f'{path.name}:{line} — у комментария {{# нет закрывающего #}}')
                self.assertNotIn(
                    '\n', source[match.start():closing],
                    f'{path.name}:{line} — многострочный {{# … #}}. '
                    f'Django его не вырежет, используйте {{% comment %}}')

    def test_rendered_pages_have_no_template_syntax(self):
        for name in ('index', 'club', 'privacy'):
            with self.subTest(page=name):
                body = self.client.get(reverse(name)).content.decode('utf-8')
                self.assertNotIn('{#', body)
                self.assertNotIn('{%', body)
                self.assertNotIn('{{', body)


class PhoneTests(TestCase):
    def test_normalize(self):
        for raw in ('+7 (995) 441-20-21', '89954412021', '79954412021', '9954412021'):
            self.assertEqual(normalize_phone(raw), '+79954412021', raw)

    def test_format(self):
        self.assertEqual(format_phone('+79954412021'), '+7 (995) 441-20-21')

    def test_form_rejects_short_number(self):
        form = LeadForm({'name': 'Пётр', 'phone': '+7 (995) 441', 'consent': '1'})
        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)

    def test_form_requires_consent(self):
        form = LeadForm({'name': 'Пётр', 'phone': '+7 (995) 441-20-21'})
        self.assertFalse(form.is_valid())
        self.assertIn('consent', form.errors)


class LeadFlowTests(TestCase):
    def test_lead_is_saved_even_without_telegram(self):
        """Главное правило: заявка не теряется, даже если уведомление не ушло."""
        response = self.client.post(reverse('index'), {
            'name': 'Пётр', 'phone': '+7 (995) 441-20-21',
            'area': 'Барбершоп', 'consent': '1',
        })
        self.assertEqual(response.status_code, 302)

        lead = Lead.objects.get()
        self.assertEqual(lead.phone, '+79954412021')
        self.assertEqual(lead.source, Lead.Source.FORM)
        self.assertFalse(lead.delivered_to_telegram)

    def test_invalid_lead_is_not_saved(self):
        self.client.post(reverse('index'), {'name': '', 'phone': '123'})
        self.assertEqual(Lead.objects.count(), 0)

    def test_club_lead_requires_telegram(self):
        self.client.post(reverse('club'), {
            'name': 'Ирина', 'phone': '+7 (902) 444-55-66', 'consent': '1',
        })
        self.assertEqual(Lead.objects.count(), 0)


class ClubSubscriptionTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(
            name='Ирина', phone='+79024445566', telegram_username='irina_biz')

    def test_activate_sets_end_date(self):
        sub = ClubSubscription.objects.create(
            client=self.client_obj, plan=ClubSubscription.Plan.MONTH)
        sub.activate()
        self.assertEqual(sub.status, ClubSubscription.Status.ACTIVE)
        self.assertEqual(sub.days_left, 29)  # неполные сутки округляются вниз

    def test_second_activation_extends_not_restarts(self):
        sub = ClubSubscription.objects.create(
            client=self.client_obj, plan=ClubSubscription.Plan.MONTH)
        sub.activate()
        first_end = sub.ends_at
        sub.activate()
        self.assertEqual((sub.ends_at - first_end).days, 30)

    def test_payment_marked_once(self):
        payment = Payment.objects.create(amount=3900, purpose=Payment.Purpose.CLUB)
        self.assertTrue(payment.mark_succeeded())
        self.assertFalse(payment.mark_succeeded())


class PageTests(TestCase):
    def test_pages_open(self):
        for name in ('index', 'club', 'privacy', 'club_done', 'robots', 'sitemap'):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_survey_opens(self):
        self.assertEqual(self.client.get(reverse('survey')).status_code, 200)

    def test_survey_done_redirects_when_empty(self):
        self.assertEqual(self.client.get(reverse('survey_done')).status_code, 302)

    def test_webhook_rejects_garbage(self):
        response = self.client.post(reverse('yookassa_webhook'),
                                    data='не json', content_type='application/json')
        self.assertEqual(response.status_code, 400)


class SurveyTests(TestCase):
    """Разбор процессов: ответы, подсчёт и оценка потерь."""

    def _answers(self, **over):
        base = {
            'area': 'beauty', 'team': '2_5', 'sources': ['word', 'maps'],
            'storage': 'head', 'lost': 'day', 'reply': 'later',
            'booking': 'me', 'noshow': 'few3', 'money_view': 'rest',
            'repeat': 'none', 'vacation': 'stop', 'routine': 'h25',
            'check': 'c2000', 'clients': 'n100', 'consent': '1',
        }
        base.update(over)
        return base

    def test_survey_saved_without_contacts(self):
        """Телефон необязателен: человек имеет право просто посмотреть."""
        response = self.client.post(reverse('survey'), self._answers())
        self.assertEqual(response.status_code, 302)
        entry = Survey.objects.get()
        self.assertEqual(entry.phone, '')
        self.assertEqual(entry.answers['lost'], 'day')
        self.assertFalse(Lead.objects.exists())

    def test_phone_creates_lead(self):
        self.client.post(reverse('survey'),
                         self._answers(name='Антон', phone='89954412021'))
        lead = Lead.objects.get()
        self.assertEqual(lead.source, Lead.Source.SURVEY)
        self.assertEqual(lead.phone, '+79954412021')
        self.assertEqual(Survey.objects.get().lead, lead)

    def test_other_needs_explanation(self):
        """«Другое» без пояснения — не ответ."""
        response = self.client.post(reverse('survey'), self._answers(area='other'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Survey.objects.exists())

        self.client.post(reverse('survey'),
                         self._answers(area='other', area_other='Автосервис'))
        self.assertEqual(Survey.objects.get().area, 'Автосервис')

    def test_result_page_shown_once(self):
        self.client.post(reverse('survey'), self._answers())
        self.assertEqual(self.client.get(reverse('survey_done')).status_code, 200)
        self.assertEqual(self.client.get(reverse('survey_done')).status_code, 302)

    def test_worst_answers_give_high_scores(self):
        result = survey_logic.diagnose(self._answers())
        self.assertEqual(len(result['top']), 3)
        self.assertTrue(all(item['level'] == 'high' for item in result['top']))

    def test_calm_answers_give_nothing(self):
        calm = self._answers(storage='crm', lost='never', reply='min15',
                             booking='online', noshow='few', money_view='report',
                             repeat='auto', vacation='ok', routine='h3')
        result = survey_logic.diagnose(calm)
        self.assertEqual(result['top'], [])
        self.assertEqual(len(result['healthy']), len(survey_logic.AREAS))

    def test_money_estimate_counts_on_top_of_revenue(self):
        """Если теряется каждая пятая заявка, теряется не пятая часть
        выручки, а ещё четверть сверх заработанного."""
        answers = self._answers(lost='day', noshow='na')  # 20% и без неявок
        est = survey_logic.estimate(answers)
        self.assertEqual(est['revenue'], 200000)          # 2000 × 100
        self.assertEqual(est['lost_money'], 50000)        # 200000 × .2 / .8
        self.assertEqual(est['noshow_money'], 0)

    def test_estimate_needs_numbers(self):
        answers = self._answers()
        answers.pop('check')
        self.assertIsNone(survey_logic.estimate(answers))

    def test_readable_uses_human_words(self):
        pairs = dict(survey_logic.readable(self._answers()))
        self.assertEqual(pairs['Как часто заявка теряется?'],
                         'Каждый день что-то теряется')

    def test_export_hides_personal_data(self):
        """В выгрузку для нейросети не должны попадать имя и телефон."""
        self.client.post(reverse('survey'),
                         self._answers(name='Антон', phone='89954412021'))
        text = analysis.as_text(Survey.objects.all())
        self.assertNotIn('Антон', text)
        self.assertNotIn('9954412021', text)
        self.assertIn('Каждый день что-то теряется', text)


class TelegramLoginTests(TestCase):
    """Вход через Telegram. Проверка подписи — единственное, что отделяет
    настоящего человека от того, кто просто подставил чужой id в адрес."""

    TOKEN = '123456:TEST-TOKEN'

    def _sign(self, data, token=None):
        import hashlib, hmac
        secret = hashlib.sha256((token or self.TOKEN).encode()).digest()
        checked = '\n'.join(f'{k}={data[k]}' for k in sorted(data))
        return hmac.new(secret, checked.encode(), hashlib.sha256).hexdigest()

    def _payload(self, **over):
        import time
        data = {'id': '77777', 'first_name': 'Антон',
                'username': 'anton', 'auth_date': str(int(time.time()))}
        data.update(over)
        data['hash'] = self._sign({k: v for k, v in data.items() if k != 'hash'})
        return data

    def test_valid_login_passes(self):
        with self.settings(TELEGRAM_BOT_TOKEN=self.TOKEN):
            data = tg_service.verify_login(self._payload())
        self.assertIsNotNone(data)
        self.assertEqual(data['id'], 77777)

    def test_tampered_id_rejected(self):
        """Подменили id, подпись оставили — не должно пройти."""
        payload = self._payload()
        payload['id'] = '99999'
        with self.settings(TELEGRAM_BOT_TOKEN=self.TOKEN):
            self.assertIsNone(tg_service.verify_login(payload))

    def test_foreign_token_rejected(self):
        payload = self._payload()
        with self.settings(TELEGRAM_BOT_TOKEN='999:OTHER'):
            self.assertIsNone(tg_service.verify_login(payload))

    def test_stale_login_rejected(self):
        import time
        old = str(int(time.time()) - 60 * 60 * 30)   # 30 часов назад
        with self.settings(TELEGRAM_BOT_TOKEN=self.TOKEN):
            self.assertIsNone(tg_service.verify_login(self._payload(auth_date=old)))

    def test_no_token_rejected(self):
        with self.settings(TELEGRAM_BOT_TOKEN=None):
            self.assertIsNone(tg_service.verify_login(self._payload()))

    def test_view_rejects_forgery(self):
        response = self.client.get(reverse('club_telegram'),
                                   {'id': '1', 'auth_date': '1', 'hash': 'нет'})
        self.assertEqual(response.status_code, 400)

    def test_login_remembers_user_id(self):
        """Найдя клиента по нику, запоминаем числовой id — без него
        закрыть доступ в канал невозможно."""
        client_obj = Client.objects.create(name='Антон', phone='+79954412021',
                                           telegram_username='anton')
        with self.settings(TELEGRAM_BOT_TOKEN=self.TOKEN):
            self.client.get(reverse('club_telegram'), self._payload())
        client_obj.refresh_from_db()
        self.assertEqual(client_obj.telegram_user_id, 77777)


class StaticManifestTests(TestCase):
    """Отсутствие собранной статики не должно ронять сайт целиком.

    Историю стоит помнить: маршруты иконок вычисляли адрес при загрузке
    модуля, и одна ненайденная запись в справочнике отпечатков обрушивала
    импорт URLconf, а с ним все страницы разом. Собрать статику при этом
    тоже не выходило — collectstatic падал на тех же проверках.
    """

    def test_pages_open_without_manifest(self):
        storage = 'core.storage.ForgivingManifestStaticFilesStorage'
        with self.settings(STORAGES={
            'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': storage},
        }):
            for name in ('index', 'survey', 'club', 'privacy'):
                with self.subTest(page=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_icon_route_redirects(self):
        response = self.client.get('/favicon.ico')
        self.assertEqual(response.status_code, 301)
        self.assertIn('favicon', response['Location'])


class GetPlatinumTests(TestCase):
    """Оплата через GetPlatinum: подпись и деньги.

    Проверяем ровно то, что дороже всего ошибиться: подсчёт подписи
    и перевод рублей в копейки. Первое пропускает чужие уведомления,
    второе списывает не ту сумму.
    """

    KEY = 'TestApiKey'

    def test_checksum_follows_described_algorithm(self):
        """Подпись считается по описанию, а не по примеру.

        Пример в документации GetPlatinum противоречив: он заявляет длину
        строки 122 символа, показывает строку в 140, а приведённая
        контрольная сумма не получается ни из показанной строки, ни из
        честно отсортированной. Поэтому сверяем реализацию с описанным
        алгоритмом: сортировка ключей без учёта регистра, «ключ;значение;»,
        HMAC-SHA256, верхний регистр.
        """
        import hashlib, hmac
        params = {'mdOrder': 53082785, 'dealId': 'DEAL-12345',
                  'isSuccess': True, 'amount': 10000, 'currency': 'RUB'}
        line = ('amount;10000;currency;RUB;dealId;DEAL-12345;'
                'isSuccess;1;mdOrder;53082785;')
        expected = hmac.new(self.KEY.encode(), line.encode(),
                            hashlib.sha256).hexdigest().upper()
        self.assertEqual(gp.checksum(params, self.KEY), expected)

    def test_booleans_become_digits(self):
        """true → 1, false → 0. Иначе подпись не сойдётся никогда."""
        self.assertEqual(gp.checksum({'a': True}, self.KEY),
                         gp.checksum({'a': 1}, self.KEY))
        self.assertEqual(gp.checksum({'a': False}, self.KEY),
                         gp.checksum({'a': 0}, self.KEY))

    def test_bad_checksum_passes_when_not_strict(self):
        """Пока подпись не подтверждена, несовпадение не должно лишать
        человека доступа: деньги списаны, решает /status."""
        params = {'dealId': 'НЕТ-ТАКОГО', 'checksum': 'подделка'}
        with self.settings(GETPLATINUM_API_KEY=self.KEY,
                           GETPLATINUM_STRICT_CHECKSUM=False):
            response = self.client.post(reverse('getplatinum_webhook'),
                                        data=json.dumps(params),
                                        content_type='application/json')
        self.assertEqual(response.status_code, 200)

    def test_verify_rejects_tampered_amount(self):
        params = {'dealId': 'DEAL-1', 'amount': 10000, 'isSuccess': True}
        params['checksum'] = gp.checksum(params, self.KEY)
        params['amount'] = 1          # подменили сумму, подпись оставили
        with self.settings(GETPLATINUM_API_KEY=self.KEY):
            self.assertFalse(gp.verify(params))

    def test_verify_accepts_honest_notification(self):
        params = {'dealId': 'DEAL-1', 'amount': 10000, 'isSuccess': True}
        params['checksum'] = gp.checksum(params, self.KEY)
        with self.settings(GETPLATINUM_API_KEY=self.KEY):
            self.assertTrue(gp.verify(params))

    def test_custom_params_excluded_from_signature(self):
        """customParams в подсчёт не входит — иначе подпись не сойдётся."""
        base = {'dealId': 'DEAL-1', 'amount': 100}
        with_custom = dict(base, customParams={'payment_pk': '7'})
        self.assertEqual(gp.checksum(base, self.KEY),
                         gp.checksum(with_custom, self.KEY))

    def test_roubles_to_kopecks(self):
        self.assertEqual(gp.to_kopecks(3900), 390000)
        self.assertEqual(gp.to_kopecks(3900.50), 390050)
        # Округление вверх, а не отбрасывание: 39.995 в двоичной дроби
        # хранится чуть меньше себя, и через float вышло бы 3999.
        self.assertEqual(gp.to_kopecks('39.995'), 4000)
        self.assertEqual(gp.to_kopecks(Decimal('3900.00')), 390000)

    def test_webhook_rejects_bad_signature_when_strict(self):
        with self.settings(GETPLATINUM_API_KEY=self.KEY,
                           GETPLATINUM_STRICT_CHECKSUM=True):
            response = self.client.post(
                reverse('getplatinum_webhook'),
                data=json.dumps({'dealId': 'X', 'checksum': 'подделка'}),
                content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_webhook_answers_200_on_unknown_deal(self):
        """GetPlatinum не повторяет попытку при коде, отличном от 200.
        Значит даже на незнакомый заказ надо ответить 200, иначе
        уведомление потеряется навсегда."""
        params = {'dealId': 'НЕТ-ТАКОГО', 'isSuccess': True}
        params['checksum'] = gp.checksum(params, self.KEY)
        with self.settings(GETPLATINUM_API_KEY=self.KEY):
            response = self.client.post(reverse('getplatinum_webhook'),
                                        data=json.dumps(params),
                                        content_type='application/json')
        self.assertEqual(response.status_code, 200)

    def test_base_url_built_from_anything_sensible(self):
        """Адрес собирается из имени аккаунта в любом виде.

        В личном кабинете готового адреса нет, и собрать его руками —
        отдельный повод ошибиться. Ошибка при этом выглядит как «ключ не
        принят», хотя ключ верный, и человек ищет не там.
        """
        want = 'https://poryadok.getplatinum.ru/api/public/pay'
        for given in ('poryadok',
                      'poryadok.getplatinum.ru',
                      'https://poryadok.getplatinum.ru',
                      'https://poryadok.getplatinum.ru/',
                      'https://poryadok.getplatinum.ru/api/public/pay',
                      # Так адрес выглядит, если скопировать его прямо из
                      # адресной строки кабинета — путь надо отбросить.
                      'https://poryadok.getplatinum.ru/cabinet/request/manage/3777/1',
                      'poryadok.getplatinum.ru/cabinet/settings',
                      'http://poryadok.getplatinum.ru/cabinet/'):
            with self.subTest(given=given):
                with self.settings(GETPLATINUM_BASE_URL=given,
                                   GETPLATINUM_ACCOUNT=None):
                    self.assertEqual(gp.base_url(), want)

        with self.settings(GETPLATINUM_BASE_URL=None,
                           GETPLATINUM_ACCOUNT='poryadok'):
            self.assertEqual(gp.base_url(), want)

    def test_disabled_without_settings(self):
        with self.settings(GETPLATINUM_API_KEY=None, GETPLATINUM_BASE_URL=None,
                           GETPLATINUM_ACCOUNT=None):
            self.assertFalse(gp.is_enabled())


class SignatureRobustnessTests(TestCase):
    """Проверка подписи не должна падать от чужого мусора.

    Подпись приходит снаружи: в теле уведомления или в адресной строке.
    Прислать туда можно что угодно, включая кириллицу и пустоту. Ошибка
    500 в ответ — это подсказка тому, кто щупает сайт, и повод для
    платёжной системы считать наш обработчик сломанным.
    """

    def test_getplatinum_survives_non_ascii_checksum(self):
        with self.settings(GETPLATINUM_API_KEY='ключ'):
            self.assertFalse(gp.verify({'a': 1, 'checksum': 'подпись'}))
            self.assertFalse(gp.verify({'a': 1, 'checksum': '🙂'}))
            self.assertFalse(gp.verify({'checksum': ''}))
            self.assertFalse(gp.verify({}))
            self.assertFalse(gp.verify(None))

    def test_telegram_survives_non_ascii_hash(self):
        with self.settings(TELEGRAM_BOT_TOKEN='123:ABC'):
            self.assertIsNone(tg_service.verify_login(
                {'id': '1', 'auth_date': '1', 'hash': 'подпись'}))
            self.assertIsNone(tg_service.verify_login({}))


class SettlePaymentTests(TestCase):
    """Ручное проведение платежа, когда уведомление потерялось.

    Уведомление ненадёжно по своей природе, а GetPlatinum повторных
    попыток не делает: деньги списаны, доступ не выдан. Значит должен
    быть способ спросить платёжную систему напрямую.
    """

    def _payment(self, deal_id='CLUB-77'):
        client = Client.objects.create(name='Антон', phone='+79954412021')
        subscription = ClubSubscription.objects.create(
            client=client, plan=ClubSubscription.Plan.MONTH, price=10)
        payment = Payment.objects.create(
            client=client, amount=10, purpose=Payment.Purpose.CLUB,
            provider='getplatinum', provider_payment_id=deal_id)
        subscription.payment = payment
        subscription.save(update_fields=['payment', 'updated_at'])
        return payment, subscription

    def test_paid_order_activates_subscription(self):
        from unittest.mock import patch
        payment, subscription = self._payment()
        out = StringIO()
        with self.settings(GETPLATINUM_API_KEY='k', GETPLATINUM_ACCOUNT='test'), \
             patch('landing.services.getplatinum.fetch_status',
                   return_value={'isSuccess': True, 'amount': 1000}), \
             patch('landing.services.telegram.create_club_invite',
                   return_value='https://t.me/+test'), \
             patch('landing.services.telegram.notify', return_value=True):
            call_command('settle_payment', 'CLUB-77', '--apply', stdout=out)

        payment.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(subscription.status, ClubSubscription.Status.ACTIVE)
        self.assertEqual(subscription.invite_link, 'https://t.me/+test')

    def test_without_apply_nothing_changes(self):
        """Без --apply команда только показывает. Случайно провести
        неоплаченное или чужое не должно быть возможно одной опечаткой."""
        from unittest.mock import patch
        payment, subscription = self._payment()
        with self.settings(GETPLATINUM_API_KEY='k', GETPLATINUM_ACCOUNT='test'), \
             patch('landing.services.getplatinum.fetch_status',
                   return_value={'isSuccess': True}):
            call_command('settle_payment', 'CLUB-77', stdout=StringIO())

        payment.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(subscription.status, ClubSubscription.Status.PENDING)

    def test_unpaid_order_left_alone(self):
        from unittest.mock import patch
        payment, _ = self._payment()
        with self.settings(GETPLATINUM_API_KEY='k', GETPLATINUM_ACCOUNT='test'), \
             patch('landing.services.getplatinum.fetch_status',
                   return_value={'isSuccess': False}):
            call_command('settle_payment', 'CLUB-77', '--apply', stdout=StringIO())
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)

    def test_second_run_does_not_extend_twice(self):
        """Повторный запуск не должен продлевать подписку ещё раз."""
        from unittest.mock import patch
        payment, subscription = self._payment()
        with self.settings(GETPLATINUM_API_KEY='k', GETPLATINUM_ACCOUNT='test'), \
             patch('landing.services.getplatinum.fetch_status',
                   return_value={'isSuccess': True}), \
             patch('landing.services.telegram.create_club_invite', return_value=None), \
             patch('landing.services.telegram.notify', return_value=True):
            call_command('settle_payment', 'CLUB-77', '--apply', stdout=StringIO())
            subscription.refresh_from_db()
            first_end = subscription.ends_at
            call_command('settle_payment', 'CLUB-77', '--apply', stdout=StringIO())

        subscription.refresh_from_db()
        self.assertEqual(subscription.ends_at, first_end)
