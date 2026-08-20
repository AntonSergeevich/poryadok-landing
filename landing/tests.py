"""Проверки, которые ловят поломки до того, как их увидят клиенты.

Запуск: python manage.py test landing
"""
import re
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from django.contrib.auth.models import User

from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile

from . import cabinet as cabinet_views
from . import constructor as build
from . import survey as survey_logic
from .forms import LeadForm
from .models import (STAGE_PLAN, Client, ClubSubscription, Lead, Message,
                     MessageFile, Payment,
                     Project, Stage, StageTask, Survey,
                     format_phone, normalize_phone)
import json
from decimal import Decimal
from io import StringIO

from django.core.management import call_command

from django.utils import timezone
from datetime import timedelta

from .services import access
from .services import chat
from .services import notify
from .services import analysis
from .services import club as club_service
from .services import getplatinum as gp
from .services import telegram as tg_service
from .works import WORKS

TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates' / 'landing'


class TemplateCommentTests(TestCase):
    """Django вырезает {# ... #} только в пределах одной строки.

    Многострочный комментарий молча попадает на страницу как текст. Один раз
    он добавил странице 300px горизонтальной прокрутки, второй — сдвинул
    портрет на 128px вниз. Оба раза это заметили только глазами.
    """

    def test_no_multiline_short_comments(self):
        # rglob, а не glob: шаблоны кабинета лежат подпапкой, и до них
        # проверка раньше не доходила.
        for path in sorted(TEMPLATES_DIR.rglob('*.html')):
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

    def test_finds_payment_with_lost_deal_id(self):
        """Номер заказа мог не сохраниться из-за сбоя, а деньги списаться.

        Такой платёж всё равно надо находить: номер заказа мы составляем
        сами как CLUB-<номер платежа>, значит его можно восстановить.
        """
        from unittest.mock import patch
        payment, subscription = self._payment(deal_id='')
        with self.settings(GETPLATINUM_API_KEY='k', GETPLATINUM_ACCOUNT='test'), \
             patch('landing.services.getplatinum.fetch_status',
                   return_value={'isSuccess': True}), \
             patch('landing.services.telegram.create_club_invite', return_value=None), \
             patch('landing.services.telegram.notify', return_value=True):
            call_command('settle_payment', f'CLUB-{payment.pk}', '--apply',
                         stdout=StringIO())

        payment.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(payment.provider_payment_id, f'CLUB-{payment.pk}')
        self.assertEqual(subscription.status, ClubSubscription.Status.ACTIVE)

    def test_all_includes_payments_without_deal_id(self):
        from unittest.mock import patch
        self._payment(deal_id='')
        out = StringIO()
        with self.settings(GETPLATINUM_API_KEY='k', GETPLATINUM_ACCOUNT='test'), \
             patch('landing.services.getplatinum.fetch_status',
                   return_value={'isSuccess': False}):
            call_command('settle_payment', '--all', stdout=out)
        self.assertNotIn('Висящих платежей нет', out.getvalue())

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


class ClubLifecycleTests(TestCase):
    """Срок доступа: напоминание, закрытие, продление.

    Три вещи, которые дороже всего сломать: не напомнить, не закрыть
    и сжечь оплаченные дни при досрочном продлении.
    """

    def _subscription(self, days_left=2, **over):
        client = Client.objects.create(
            name='Пётр', phone='+79001234567', telegram_user_id=555, **over)
        return ClubSubscription.objects.create(
            client=client, plan=ClubSubscription.Plan.MONTH, price=3900,
            status=ClubSubscription.Status.ACTIVE,
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(days=days_left))

    def test_reminder_goes_to_member_and_owner(self):
        from unittest.mock import patch
        subscription = self._subscription(days_left=2)
        with patch('landing.services.telegram.send_to', return_value=True) as to_member, \
             patch('landing.services.telegram.notify', return_value=True) as to_owner:
            call_command('club_reminders', stdout=StringIO())

        self.assertTrue(to_member.called)
        self.assertTrue(to_owner.called)
        subscription.refresh_from_db()
        self.assertIsNotNone(subscription.reminded_at)

    def test_reminder_not_repeated_next_day(self):
        """Ежедневный запуск не должен слать одно и то же каждый день."""
        from unittest.mock import patch
        self._subscription(days_left=2)
        with patch('landing.services.telegram.send_to', return_value=True), \
             patch('landing.services.telegram.notify', return_value=True):
            call_command('club_reminders', stdout=StringIO())
            out = StringIO()
            call_command('club_reminders', stdout=out)
        self.assertIn('Некого предупреждать', out.getvalue())

    def test_far_away_subscription_left_alone(self):
        from unittest.mock import patch
        self._subscription(days_left=20)
        out = StringIO()
        with patch('landing.services.telegram.send_to') as to_member:
            call_command('club_reminders', stdout=out)
        self.assertFalse(to_member.called)
        self.assertIn('Некого предупреждать', out.getvalue())

    def test_expired_subscription_closed_and_member_told(self):
        from unittest.mock import patch
        subscription = self._subscription(days_left=-1)
        with patch('landing.services.telegram.remove_from_club',
                   return_value=True) as kick, \
             patch('landing.services.telegram.send_to', return_value=True) as bye, \
             patch('landing.services.telegram.notify', return_value=True):
            call_command('expire_club', stdout=StringIO())

        subscription.refresh_from_db()
        self.assertEqual(subscription.status, ClubSubscription.Status.EXPIRED)
        kick.assert_called_once_with(555)
        self.assertTrue(bye.called)

    def test_dry_run_changes_nothing(self):
        from unittest.mock import patch
        subscription = self._subscription(days_left=-1)
        with patch('landing.services.telegram.remove_from_club') as kick:
            call_command('expire_club', '--dry-run', stdout=StringIO())
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, ClubSubscription.Status.ACTIVE)
        self.assertFalse(kick.called)

    def test_early_renewal_does_not_burn_paid_days(self):
        """Продлил за неделю до конца — эта неделя должна остаться."""
        from unittest.mock import patch
        running = self._subscription(days_left=7)
        client = running.client

        renewal = ClubSubscription.objects.create(
            client=client, plan=ClubSubscription.Plan.MONTH, price=3900)
        payment = Payment.objects.create(
            client=client, amount=3900, purpose=Payment.Purpose.CLUB,
            provider='getplatinum', provider_payment_id='CLUB-99')
        renewal.payment = payment
        renewal.save(update_fields=['payment', 'updated_at'])

        with patch('landing.services.telegram.create_club_invite', return_value=None), \
             patch('landing.services.telegram.notify', return_value=True):
            club_service.grant_access(payment)

        renewal.refresh_from_db()
        # 7 оставшихся дней + 30 новых, а не 30 от сегодня.
        self.assertGreater(renewal.days_left, 35)


class BotWebhookTests(TestCase):
    """Вход через бота — замена устаревшему виджету Telegram.

    Ради одного: узнать числовой id человека. По нику Telegram не даёт
    ни писать, ни исключать из канала.
    """

    SECRET = 'секрет-подлиннее-и-без-пробелов'

    def _post(self, payload, secret=None, header=None):
        return self.client.post(
            reverse('telegram_bot_webhook', args=[secret or self.SECRET]),
            data=json.dumps(payload), content_type='application/json',
            headers={'X-Telegram-Bot-Api-Secret-Token': header or self.SECRET})

    def test_wrong_secret_rejected(self):
        with self.settings(TELEGRAM_WEBHOOK_SECRET=self.SECRET):
            self.assertEqual(self._post({}, secret='чужой').status_code, 400)
            self.assertEqual(self._post({}, header='чужой').status_code, 400)

    def test_no_secret_configured_rejects_everything(self):
        """Пока секрет не задан, обработчик не должен принимать ничего."""
        with self.settings(TELEGRAM_WEBHOOK_SECRET=''):
            self.assertEqual(self._post({}, secret='', header='').status_code, 400)

    def test_start_asks_for_phone(self):
        from unittest.mock import patch
        with self.settings(TELEGRAM_WEBHOOK_SECRET=self.SECRET), \
             patch('landing.services.telegram.ask_contact', return_value=True) as ask:
            self._post({'message': {'chat': {'id': 42}, 'text': '/start club'}})
        self.assertTrue(ask.called)

    def test_contact_links_client_and_sends_invite(self):
        from unittest.mock import patch
        client_obj = Client.objects.create(name='Пётр', phone='+79001234567')
        ClubSubscription.objects.create(
            client=client_obj, plan=ClubSubscription.Plan.MONTH, price=3900,
            status=ClubSubscription.Status.ACTIVE,
            ends_at=timezone.now() + timedelta(days=20))

        with self.settings(TELEGRAM_WEBHOOK_SECRET=self.SECRET), \
             patch('landing.services.telegram.create_club_invite',
                   return_value='https://t.me/+abc') as invite, \
             patch('landing.services.telegram.reply', return_value=True) as answer:
            self._post({'message': {
                'chat': {'id': 42},
                'from': {'id': 777, 'username': 'petr'},
                'contact': {'phone_number': '89001234567', 'user_id': 777},
            }})

        client_obj.refresh_from_db()
        self.assertEqual(client_obj.telegram_user_id, 777)
        self.assertEqual(client_obj.telegram_username, 'petr')
        self.assertTrue(invite.called)
        self.assertIn('t.me/+abc', answer.call_args[0][1])

    def test_someone_elses_contact_ignored(self):
        """Переслать чужую карточку можно, но user_id в ней не совпадёт
        с отправителем — значит связывать нечего."""
        from unittest.mock import patch
        client_obj = Client.objects.create(name='Пётр', phone='+79001234567')
        with self.settings(TELEGRAM_WEBHOOK_SECRET=self.SECRET), \
             patch('landing.services.telegram.reply', return_value=True):
            self._post({'message': {
                'chat': {'id': 42},
                'from': {'id': 999},
                'contact': {'phone_number': '89001234567', 'user_id': 777},
            }})
        client_obj.refresh_from_db()
        self.assertIsNone(client_obj.telegram_user_id)

    def test_unknown_phone_gets_polite_answer(self):
        from unittest.mock import patch
        with self.settings(TELEGRAM_WEBHOOK_SECRET=self.SECRET), \
             patch('landing.services.telegram.reply', return_value=True) as answer:
            self._post({'message': {
                'chat': {'id': 42},
                'from': {'id': 777},
                'contact': {'phone_number': '89998887766', 'user_id': 777},
            }})
        self.assertIn('не нашёл', answer.call_args[0][1])


class CabinetAccessTests(TestCase):
    """Выдача доступа: логин, пароль, повторная выдача, закрытие."""

    def setUp(self):
        self.client_card = Client.objects.create(
            name='Ирина Величко', phone='+79130000001')

    def test_login_is_transliterated_name(self):
        issued = access.issue(self.client_card)
        self.assertEqual(issued['login'], 'irinavelichko')
        self.assertTrue(self.client_card.user)

    def test_second_client_with_same_name_gets_number(self):
        access.issue(self.client_card)
        twin = Client.objects.create(name='Ирина Величко', phone='+79130000002')
        self.assertEqual(access.issue(twin)['login'], 'irinavelichko2')

    def test_password_has_no_confusable_characters(self):
        # Пароль диктуют голосом. 0/O и 1/l/I в разговоре неразличимы.
        for _ in range(50):
            self.assertFalse(set(access.make_password()) & set('0O1lI'))

    def test_password_is_stored_hashed_only(self):
        issued = access.issue(self.client_card)
        user = self.client_card.user
        user.refresh_from_db()
        self.assertNotIn(issued['password'], user.password)
        self.assertTrue(user.check_password(issued['password']))

    def test_reissue_keeps_the_same_account(self):
        first = access.issue(self.client_card)
        user_id = self.client_card.user_id
        second = access.issue(self.client_card)

        self.client_card.refresh_from_db()
        self.assertEqual(self.client_card.user_id, user_id)
        self.assertEqual(first['login'], second['login'])
        self.assertNotEqual(first['password'], second['password'])
        self.client_card.user.refresh_from_db()
        self.assertTrue(self.client_card.user.check_password(second['password']))

    def test_revoke_closes_the_door_but_keeps_the_account(self):
        access.issue(self.client_card)
        self.assertTrue(access.revoke(self.client_card))
        self.client_card.refresh_from_db()
        self.assertIsNotNone(self.client_card.user)
        self.assertFalse(self.client_card.user.is_active)

    def test_reissue_opens_a_closed_door(self):
        # «Выдать доступ» и «потерялся пароль» — одна кнопка. Разбираться,
        # какая из них сейчас нужна, человек не должен.
        access.issue(self.client_card)
        access.revoke(self.client_card)
        access.issue(self.client_card)
        self.client_card.user.refresh_from_db()
        self.assertTrue(self.client_card.user.is_active)

    def test_ready_message_carries_everything_needed(self):
        issued = access.issue(self.client_card)
        for piece in (issued['login'], issued['password'], issued['url']):
            self.assertIn(piece, issued['text'])
        self.assertIn('Ирина', issued['text'])


class StageTests(TestCase):
    """Этапы: раскладка, текущий, доля пути, заливка шкалы."""

    def setUp(self):
        self.card = Client.objects.create(name='Пётр', phone='+79130000003')
        self.project = Project.objects.create(client=self.card, title='Система')
        self.project.build_stages()

    def test_stages_are_laid_out_once(self):
        self.assertEqual(self.project.stages.count(), len(STAGE_PLAN))
        self.assertEqual(self.project.build_stages(), 0)
        self.assertEqual(self.project.stages.count(), len(STAGE_PLAN))

    def test_current_is_the_running_one(self):
        third = self.project.stages.get(number=3)
        third.mark(Stage.Status.RUNNING)
        self.assertEqual(self.project.current_stage.number, 3)

    def test_current_falls_back_to_first_unfinished(self):
        self.project.stages.filter(number__lte=2).update(status=Stage.Status.DONE)
        self.assertEqual(self.project.current_stage.number, 3)

    def test_current_never_empty_on_finished_project(self):
        # Шкале нужно что-то подсветить в любом состоянии проекта.
        self.project.stages.update(status=Stage.Status.DONE)
        self.assertEqual(self.project.current_stage.number, len(STAGE_PLAN))

    def test_progress_counts_days_not_stages(self):
        """Три закрытых этапа из восьми — это не 38% пути.

        «Разбор» занимает три дня, «запуск» — двадцать. Считать этапы
        штуками значит обещать темп, которого не будет.
        """
        for number in (1, 2, 3):
            self.project.stages.get(number=number).mark(Stage.Status.DONE)
        total = sum(days for _, _, _, days in STAGE_PLAN)
        done = sum(days for number, _, _, days in STAGE_PLAN if number <= 3)
        self.assertEqual(self.project.progress, round(done * 100 / total))
        self.assertNotEqual(self.project.progress, round(3 * 100 / 8))

    def test_marking_done_sets_dates_and_clears_waiting(self):
        stage = self.project.stages.get(number=1)
        stage.waiting_on = Stage.Waiting.CLIENT
        stage.save()
        stage.mark(Stage.Status.DONE)
        stage.refresh_from_db()
        self.assertIsNotNone(stage.started_at)
        self.assertIsNotNone(stage.finished_at)
        self.assertEqual(stage.waiting_on, '')

    def test_reopening_clears_the_finish_date(self):
        stage = self.project.stages.get(number=1)
        stage.mark(Stage.Status.DONE)
        stage.mark(Stage.Status.RUNNING)
        stage.refresh_from_db()
        self.assertIsNone(stage.finished_at)
        self.assertIsNotNone(stage.started_at)

    def test_fill_reaches_the_centre_of_the_current_dot(self):
        """Заливку сверяют глазом с точкой, а не с процентами."""
        stages = self.project.ordered_stages
        current = self.project.stages.get(number=1)
        # Первая из восьми колонок: центр на (0 + ½)/8 = 6.25%.
        self.assertEqual(cabinet_views._fill_percent(stages, current), '6.25')

    def test_fill_is_written_with_a_dot(self):
        """При русской локали число 43.75 печатается как «43,75», и
        правило width становится недействительным — заливка исчезает
        целиком. Ошибку видно только на экране, поэтому она здесь."""
        stages = self.project.ordered_stages
        value = cabinet_views._fill_percent(stages, self.project.stages.get(number=4))
        self.assertNotIn(',', value)
        self.assertEqual(value, '43.75')

    def test_finished_project_is_filled_whole(self):
        self.project.stages.update(status=Stage.Status.DONE)
        self.assertEqual(
            cabinet_views._fill_percent(self.project.ordered_stages,
                                        self.project.current_stage), '100')

    def test_client_todo_collects_only_their_open_tasks(self):
        stage = self.project.stages.get(number=1)
        StageTask.objects.create(stage=stage, title='Моё', who=StageTask.Who.ME)
        StageTask.objects.create(stage=stage, title='Ваше', who=StageTask.Who.CLIENT)
        StageTask.objects.create(stage=stage, title='Вместе', who=StageTask.Who.BOTH)
        closed = StageTask.objects.create(stage=stage, title='Закрытое',
                                          who=StageTask.Who.CLIENT)
        closed.toggle(True)

        titles = [task.title for task in self.project.client_todo()]
        self.assertEqual(sorted(titles), ['Ваше', 'Вместе'])


class CabinetViewTests(TestCase):
    """Кто что видит и кто что может."""

    def setUp(self):
        self.owner = User.objects.create_user('anton', password='x' * 12,
                                              is_staff=True)
        self.card = Client.objects.create(name='Ирина', phone='+79130000004')
        access.issue(self.card)
        self.card.refresh_from_db()
        self.card.user.set_password('y' * 12)
        self.card.user.save()

        self.project = Project.objects.create(client=self.card, title='Система')
        self.project.build_stages()
        self.stage = self.project.stages.get(number=1)
        self.mine = StageTask.objects.create(stage=self.stage, title='Моё',
                                             who=StageTask.Who.ME)
        self.theirs = StageTask.objects.create(stage=self.stage, title='Ваше',
                                               who=StageTask.Who.CLIENT)

        # Чужой клиент — проверяем, что он не дотянется до этого проекта.
        self.stranger_card = Client.objects.create(name='Гость',
                                                   phone='+79130000005')
        access.issue(self.stranger_card)
        self.stranger_card.refresh_from_db()
        self.stranger_card.user.set_password('z' * 12)
        self.stranger_card.user.save()

    def login_owner(self):
        self.client.force_login(self.owner)

    def login_client(self):
        self.client.force_login(self.card.user)

    def test_one_door_sends_owner_to_the_desk(self):
        self.login_owner()
        body = self.client.get(reverse('cabinet')).content.decode()
        self.assertIn('За что взяться сегодня', body)

    def test_one_door_sends_client_to_their_project(self):
        self.login_client()
        body = self.client.get(reverse('cabinet')).content.decode()
        self.assertIn('Ваш проект', body)
        self.assertNotIn('За что взяться сегодня', body)

    def test_stranger_is_sent_to_the_login_page(self):
        response = self.client.get(reverse('cabinet'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_client_cannot_open_the_owner_project_page(self):
        self.login_client()
        response = self.client.get(
            reverse('cabinet_project', args=[self.project.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('cabinet'))

    def test_client_closes_their_own_task(self):
        self.login_client()
        response = self.client.post(
            reverse('cabinet_task_toggle', args=[self.theirs.pk]),
            {'done': '1'}, headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(response.status_code, 200)
        self.theirs.refresh_from_db()
        self.assertTrue(self.theirs.is_done)

    def test_client_cannot_close_someone_elses_task(self):
        """Запрет живёт на сервере, а не в вёрстке: спрятанная кнопка —
        это вежливая просьба, а не запрет."""
        self.login_client()
        response = self.client.post(
            reverse('cabinet_task_toggle', args=[self.mine.pk]),
            {'done': '1'}, headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(response.status_code, 400)
        self.mine.refresh_from_db()
        self.assertFalse(self.mine.is_done)

    def test_client_cannot_touch_another_clients_project(self):
        self.client.force_login(self.stranger_card.user)
        response = self.client.post(
            reverse('cabinet_task_toggle', args=[self.theirs.pk]),
            {'done': '1'}, headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(response.status_code, 404)
        self.theirs.refresh_from_db()
        self.assertFalse(self.theirs.is_done)

    def test_client_cannot_add_or_delete_tasks(self):
        self.login_client()
        for url, payload in (
            (reverse('cabinet_task_add', args=[self.stage.pk]), {'title': 'Ой'}),
            (reverse('cabinet_task_delete', args=[self.mine.pk]), {}),
            (reverse('cabinet_stage_status', args=[self.stage.pk]),
             {'status': 'done'}),
        ):
            with self.subTest(url=url):
                response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stage.tasks.count(), 2)

    def test_action_returns_redrawn_card_and_rail(self):
        """Шкала возвращается вместе с карточкой: закрытая задача может
        закрыть этап, а закрытый этап двигает подсветку. Обновить одно
        и забыть другое значит показать экран, противоречащий сам себе."""
        self.login_owner()
        response = self.client.post(
            reverse('cabinet_stage_status', args=[self.stage.pk]),
            {'status': Stage.Status.DONE},
            headers={'x-requested-with': 'XMLHttpRequest'})
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
        self.assertIn(f'stage-{self.stage.pk}', data['stage_id'])
        self.assertIn('rail__fill', data['rail'])
        self.assertIn('Готово', data['stage'])

    def test_unknown_status_is_refused(self):
        self.login_owner()
        response = self.client.post(
            reverse('cabinet_stage_status', args=[self.stage.pk]),
            {'status': 'выдумка'},
            headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(response.status_code, 400)

    def test_grant_access_shows_the_password_exactly_once(self):
        self.login_owner()
        fresh = Client.objects.create(name='Новый', phone='+79130000006')
        self.client.post(reverse('cabinet_grant', args=[fresh.pk]),
                         {'back': reverse('cabinet')})

        first = self.client.get(reverse('cabinet')).content.decode()
        self.assertIn('Кабинет заказчика открыт', first)

        second = self.client.get(reverse('cabinet')).content.decode()
        self.assertNotIn('Кабинет заказчика открыт', second)

    def test_grant_ignores_a_foreign_return_address(self):
        """Адрес возврата приходит формой. Без проверки его можно
        подменить на чужой сайт — и кнопка «завести кабинет» станет
        переадресацией куда угодно."""
        self.login_owner()
        fresh = Client.objects.create(name='Новый', phone='+79130000007')
        response = self.client.post(
            reverse('cabinet_grant', args=[fresh.pk]),
            {'back': 'https://example.com/'})
        self.assertEqual(response.url, reverse('cabinet'))

    def test_project_is_created_with_stages(self):
        self.login_owner()
        fresh = Client.objects.create(name='Новый', phone='+79130000008')
        self.client.post(reverse('cabinet_project_create'),
                         {'client': fresh.pk, 'title': 'Новая система'})
        project = Project.objects.get(client=fresh)
        self.assertEqual(project.stages.count(), len(STAGE_PLAN))

    def test_only_the_open_stage_arrives_visible(self):
        """Восемь карточек не должны мигать на загрузке: лишние приезжают
        уже скрытыми, а без скрипта вёрстка это скрытие отменяет."""
        self.login_owner()
        body = self.client.get(
            reverse('cabinet_project', args=[self.project.pk])).content.decode()
        cards = re.findall(r'<article class="stage.*?>', body, re.S)
        self.assertEqual(len(cards), len(STAGE_PLAN))
        self.assertEqual(sum('hidden' in card for card in cards),
                         len(STAGE_PLAN) - 1)


class WorksTests(TestCase):
    """Портфолио: данные и снимки должны существовать."""

    def test_every_screenshot_file_is_in_place(self):
        base = Path(__file__).resolve().parent / 'static' / 'landing' / 'img' / 'cases'
        for work in WORKS:
            for shot in work['shots']:
                for name in (f'{shot["file"]}.webp', f'{shot["file"]}-sm.webp'):
                    with self.subTest(file=name):
                        self.assertTrue((base / name).exists(),
                                        f'нет файла {name}')

    def test_works_appear_on_the_page(self):
        body = self.client.get(reverse('index')).content.decode()
        for work in WORKS:
            self.assertIn(work['site'], body)
            self.assertIn(work['title'], body)

    def test_every_work_says_what_was_and_what_became(self):
        for work in WORKS:
            with self.subTest(work=work['slug']):
                self.assertTrue(work['was'], 'кейс без «было» — это не кейс')
                self.assertTrue(work['now'])


class ConstructorTests(TestCase):
    """Конструктор: расчёт, подсказка с разбора, заявка с составом."""

    def test_core_is_always_in_and_cannot_be_dropped(self):
        # Выключить ядро нельзя. Спорить об этом с тем, кто правит адрес
        # руками, незачем — оно просто дописывается.
        for chosen in ([], ['leads'], ['выдумка']):
            with self.subTest(chosen=chosen):
                self.assertIn('core', build.clean(chosen))

    def test_unknown_blocks_are_dropped(self):
        self.assertEqual(build.clean(['leads', 'нет-такого']), ['core', 'leads'])

    def test_order_does_not_depend_on_clicking_order(self):
        """Схема должна выглядеть одинаково при одинаковом наборе."""
        first = build.clean(['payments', 'leads', 'booking'])
        second = build.clean(['booking', 'payments', 'leads'])
        self.assertEqual(first, second)

    def test_core_alone_costs_the_base_price(self):
        self.assertEqual(build.estimate([])['total'], build.BASE_PRICE)

    def test_price_grows_with_blocks(self):
        one = build.estimate(['leads'])['total']
        two = build.estimate(['leads', 'booking'])['total']
        self.assertGreater(two, one)

    def test_bundle_discount_switches_tiers(self):
        """Пятый блок собирается вчетверо быстрее первого: общая часть
        уже сделана. Не поделиться этим значит брать деньги за то,
        чего не делаешь."""
        four = build.estimate(['leads', 'booking', 'reminders', 'payments'])
        six = build.estimate(['leads', 'booking', 'reminders', 'payments',
                              'money', 'cabinet'])
        self.assertEqual(four['discount'], 7)
        self.assertEqual(six['discount'], 12)

    def test_scale_raises_the_price(self):
        solo = build.estimate(['leads'], 'solo')['total']
        multi = build.estimate(['leads'], 'multi')['total']
        self.assertGreater(multi, solo)

    def test_unknown_scale_falls_back_to_solo(self):
        self.assertEqual(build.estimate(['leads'], 'выдумка')['scale']['id'],
                         'solo')

    def test_total_is_rounded_up_to_thousands(self):
        """«163 750 ₽» выглядит как расчёт, которого никто не делал."""
        for scale in ('solo', 'team', 'multi'):
            for chosen in ([], ['leads'], ['leads', 'booking', 'reminders',
                                           'payments', 'money']):
                with self.subTest(scale=scale, chosen=chosen):
                    total = build.estimate(chosen, scale)['total']
                    self.assertEqual(total % 1000, 0)

    def test_discount_never_makes_it_cheaper_than_the_core(self):
        every = [block['id'] for block in build.BLOCKS]
        self.assertGreater(build.estimate(every)['total'], build.BASE_PRICE)

    def test_suggestion_follows_the_pains_found(self):
        diagnosis = {'top': [{'key': 'schedule'}, {'key': 'money'}]}
        picked = build.suggest(diagnosis)
        # Запись закрывает «расписание», оплаты — «деньги».
        self.assertIn('booking', picked)
        self.assertIn('payments', picked)
        # А сайт не закрывает ни того, ни другого — и остаётся выключенным.
        self.assertNotIn('site', picked)
        self.assertIn('core', picked)

    def test_suggestion_without_a_diagnosis_is_just_the_core(self):
        self.assertEqual(build.suggest(None), build.core_ids())
        self.assertEqual(build.suggest({'top': []}), build.core_ids())

    def test_page_opens_and_shows_every_block(self):
        body = self.client.get(reverse('constructor')).content.decode()
        for block in build.BLOCKS:
            self.assertIn(block['title'], body)

    def test_price_endpoint_returns_a_rendered_total(self):
        """Итог приходит разметкой, а не набором цифр: пока он собирается
        в двух местах, строка про скидку однажды снова потеряется."""
        response = self.client.post(
            reverse('constructor_price'),
            {'blocks': ['leads', 'booking', 'reminders', 'payments',
                        'money', 'cabinet'], 'scale': 'team'})
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
        self.assertIn('total__fig', data['html'])
        self.assertIn('−12%'.replace('−', '−'), data['html'].replace('&minus;', '−'))

    def test_lead_carries_the_composition(self):
        """Заявка «расскажите про цены» и заявка «нужны запись, оплаты
        и напоминания, примерно 190 000» — два разных разговора."""
        self.client.post(reverse('constructor'), {
            'blocks': ['leads', 'booking'],
            'scale': 'solo',
            'name': 'Пётр',
            'phone': '+7 913 000-00-09',
            'area': 'Барбершоп',
            'consent': 'on',
        })
        lead = Lead.objects.get(phone='+79130000009')
        self.assertEqual(lead.source, Lead.Source.BUILD)
        self.assertIn('Заявки', lead.comment)
        self.assertIn('Запись и расписание', lead.comment)
        self.assertIn('₽', lead.comment)

    def test_lead_is_not_saved_without_consent(self):
        self.client.post(reverse('constructor'), {
            'blocks': ['leads'], 'scale': 'solo',
            'name': 'Пётр', 'phone': '+7 913 000-00-10',
        })
        self.assertFalse(Lead.objects.filter(phone='+79130000010').exists())

    def test_survey_result_prepares_the_suggestion(self):
        entry = Survey.objects.create(answers={'lost': 'often'})
        session = self.client.session
        session['survey_id'] = entry.pk
        session.save()

        self.client.get(reverse('survey_done'))
        self.assertIn('build_suggest', self.client.session)

        # На самой странице конструктора подсказка уже применена.
        body = self.client.get(reverse('constructor')).content.decode()
        self.assertNotIn('build_suggest', self.client.session)
        self.assertIn('Из чего собираем', body)

    def test_price_endpoint_refuses_get(self):
        self.assertEqual(
            self.client.get(reverse('constructor_price')).status_code, 405)


class ChatTests(TestCase):
    """Переписка: доказательная база, а не удобство."""

    def setUp(self):
        self.owner = User.objects.create_user('anton', password='x' * 12,
                                              is_staff=True)
        self.card = Client.objects.create(name='Ирина', phone='+79130000020')
        access.issue(self.card)
        self.card.refresh_from_db()
        self.card.user.set_password('y' * 12)
        self.card.user.save()
        self.project = Project.objects.create(client=self.card, title='Система')

        self.stranger = Client.objects.create(name='Гость', phone='+79130000021')
        access.issue(self.stranger)
        self.stranger.refresh_from_db()

    def post(self, project, user, **payload):
        self.client.force_login(user)
        return self.client.post(
            reverse('cabinet_chat_send', args=[project.pk]), payload,
            headers={'x-requested-with': 'XMLHttpRequest'})

    def test_owner_is_signed_by_name_not_login(self):
        """«anton» — это то, чем входят, а не то, как зовут. Заказчик,
        которому пишет «anton», не понимает, с кем разговаривает."""
        self.post(self.project, self.owner, text='Здравствуйте')
        message = Message.objects.get()
        self.assertNotEqual(message.author_name, 'anton')
        self.assertEqual(message.author_name, settings.SITE_OWNER)

    def test_client_is_signed_by_card_name(self):
        self.post(self.project, self.card.user, text='Добрый день')
        self.assertEqual(Message.objects.get().author_name, 'Ирина')

    def test_name_is_frozen_at_send_time(self):
        """Учётную запись можно переименовать, а переписка обязана
        остаться читаемой."""
        self.post(self.project, self.card.user, text='Первое')
        self.card.name = 'Ирина Петровна'
        self.card.save()
        self.post(self.project, self.card.user, text='Второе')

        names = list(Message.objects.order_by('pk')
                     .values_list('author_name', flat=True))
        self.assertEqual(names, ['Ирина', 'Ирина Петровна'])

    def test_empty_message_is_not_saved(self):
        """Случайный Enter не должен оставлять в доказательной базе
        пустые строки."""
        response = self.post(self.project, self.owner, text='   ')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Message.objects.count(), 0)

    def test_stranger_cannot_read_or_write(self):
        self.post(self.project, self.owner, text='Внутреннее')
        self.client.force_login(self.stranger.user)

        write = self.client.post(
            reverse('cabinet_chat_send', args=[self.project.pk]),
            {'text': 'Подсмотрю'},
            headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(write.status_code, 404)

        read = self.client.get(
            reverse('cabinet_chat_since', args=[self.project.pk]) + '?after=0')
        self.assertEqual(read.status_code, 404)
        self.assertEqual(Message.objects.count(), 1)

    def test_since_returns_only_what_is_new(self):
        """Один короткий запрос вместо перезагрузки ленты."""
        self.post(self.project, self.owner, text='Первое')
        first = Message.objects.get().pk
        self.post(self.project, self.owner, text='Второе')

        self.client.force_login(self.card.user)
        data = json.loads(self.client.get(
            reverse('cabinet_chat_since', args=[self.project.pk]),
            {'after': first},
            headers={'x-requested-with': 'XMLHttpRequest'}).content)
        self.assertEqual(len(data['items']), 1)
        self.assertIn('Второе', data['items'][0])
        self.assertNotIn('Первое', data['items'][0])

    def test_reading_marks_the_other_sides_messages(self):
        self.post(self.project, self.owner, text='Посмотрите')
        self.assertIsNone(Message.objects.get().read_at)

        self.client.force_login(self.card.user)
        self.client.get(reverse('cabinet_chat_since', args=[self.project.pk]),
                        {'after': 0},
                        headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertIsNotNone(Message.objects.get().read_at)

    def test_own_messages_are_never_marked_read_by_yourself(self):
        """«Прочитано» ставит тот, кто читает. Иначе оно означало бы
        «доставлено», а это разные вещи."""
        self.post(self.project, self.owner, text='Своё')
        self.client.force_login(self.owner)
        self.client.get(reverse('cabinet_chat_since', args=[self.project.pk]),
                        {'after': 0},
                        headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertIsNone(Message.objects.get().read_at)

    def test_read_ids_come_back_without_any_new_message(self):
        """Отметка меняется, когда собеседник просто открыл кабинет.
        Без отдельного списка она появлялась бы только после
        перезагрузки страницы, то есть почти никогда."""
        self.post(self.project, self.owner, text='Посмотрите')
        mine = Message.objects.get().pk

        self.client.force_login(self.card.user)
        self.client.get(reverse('cabinet_chat_since', args=[self.project.pk]),
                        {'after': mine},
                        headers={'x-requested-with': 'XMLHttpRequest'})

        self.client.force_login(self.owner)
        data = json.loads(self.client.get(
            reverse('cabinet_chat_since', args=[self.project.pk]),
            {'after': mine},
            headers={'x-requested-with': 'XMLHttpRequest'}).content)
        self.assertEqual(data['items'], [])
        self.assertIn(mine, data['read'])

    def test_sides_are_decided_on_the_server(self):
        """«Своё справа» зависит от того, кто смотрит. Решать это
        в двух местах — способ однажды показать заказчику его же
        сообщения слева."""
        self.post(self.project, self.owner, text='От меня')

        self.client.force_login(self.owner)
        mine = self.client.get(
            reverse('cabinet_project', args=[self.project.pk])).content.decode()
        self.assertIn('msg--mine', mine)

        self.client.force_login(self.card.user)
        theirs = self.client.get(reverse('cabinet_mine')).content.decode()
        self.assertNotIn('msg--mine', theirs)
        self.assertIn(settings.SITE_OWNER, theirs)

    def test_attachment_is_stored_with_the_message(self):
        upload = SimpleUploadedFile('smeta.txt', b'a' * 200, 'text/plain')
        self.client.force_login(self.owner)
        self.client.post(reverse('cabinet_chat_send', args=[self.project.pk]),
                         {'text': 'Смета', 'files': upload},
                         headers={'x-requested-with': 'XMLHttpRequest'})

        file = MessageFile.objects.get()
        self.assertEqual(file.name, 'smeta.txt')
        self.assertEqual(file.size, 200)
        self.assertEqual(file.message.text, 'Смета')

    def test_oversized_file_is_refused_not_stored(self):
        big = SimpleUploadedFile('video.mp4', b'x' * (chat.MAX_FILE + 1),
                                 'video/mp4')
        response = self.post(self.project, self.owner, files=big)
        # Текста нет и файл не принят — сохранять нечего.
        self.assertEqual(response.status_code, 400)
        self.assertEqual(MessageFile.objects.count(), 0)

    def test_human_size_is_readable(self):
        for size, expected in ((512, '512 Б'), (2048, '2 КБ'),
                               (2_517_948, '2,4 МБ')):
            with self.subTest(size=size):
                file = MessageFile(name='f', size=size)
                self.assertEqual(file.human_size, expected)

    def test_older_loads_what_came_before(self):
        for number in range(chat.PAGE + 5):
            self.post(self.project, self.owner, text=f'Сообщение {number}')

        self.client.force_login(self.owner)
        body = self.client.get(
            reverse('cabinet_project', args=[self.project.pk])).content.decode()
        # В ленту сразу приезжает только хвост: год переписки одним
        # запросом — это секунда ожидания и мегабайт с телефона.
        self.assertNotIn('Сообщение 0<', body)
        self.assertIn('Показать раньше', body)

        first = chat.tail(self.project)[0].pk
        data = json.loads(self.client.get(
            reverse('cabinet_chat_older', args=[self.project.pk]),
            {'before': first},
            headers={'x-requested-with': 'XMLHttpRequest'}).content)
        self.assertTrue(data['items'])
        self.assertFalse(data['more'])


class NotifyTests(TestCase):
    """Уведомление уходит той стороне, которой адресовано."""

    def setUp(self):
        self.owner = User.objects.create_user('anton', password='x' * 12,
                                              is_staff=True)
        self.card = Client.objects.create(name='Ирина', phone='+79130000030',
                                          telegram_user_id=555)
        access.issue(self.card)
        self.card.refresh_from_db()
        self.project = Project.objects.create(client=self.card, title='Система')
        self.project.build_stages()
        self.stage = self.project.stages.get(number=1)

    def test_stage_move_writes_to_the_client(self):
        with mock.patch.object(tg_service, 'send_to', return_value=True) as sent:
            notify.stage_moved(self.stage)
        sent.assert_called_once()
        self.assertEqual(sent.call_args[0][0], 555)
        self.assertIn(self.stage.title, sent.call_args[0][1])

    def test_nothing_is_sent_when_telegram_is_not_linked(self):
        """Пока номер не связан с ботом, писать некуда — и кабинет
        от этого работать не перестаёт."""
        self.card.telegram_user_id = None
        self.card.save()
        with mock.patch.object(tg_service, 'send_to') as sent:
            self.assertFalse(notify.stage_moved(self.stage))
        sent.assert_not_called()

    def test_a_broken_notification_never_breaks_the_action(self):
        """Отметить задачу важнее, чем сообщить о ней."""
        with mock.patch.object(tg_service, 'send_to',
                               side_effect=RuntimeError('сеть')):
            self.assertFalse(notify.stage_moved(self.stage))

    def test_client_task_notifies_the_client(self):
        task = StageTask.objects.create(stage=self.stage, title='Прислать фото',
                                        who=StageTask.Who.CLIENT)
        with mock.patch.object(tg_service, 'send_to', return_value=True) as sent:
            notify.task_for_client(task)
        self.assertIn('Прислать фото', sent.call_args[0][1])

    def test_message_goes_to_the_side_that_did_not_write_it(self):
        from_owner = Message.objects.create(
            project=self.project, author=self.owner, author_name='Антон',
            author_is_owner=True, text='Посмотрите')
        with mock.patch.object(tg_service, 'send_to', return_value=True) as sent:
            notify.new_message(from_owner)
        self.assertEqual(sent.call_args[0][0], 555)   # заказчику

        from_client = Message.objects.create(
            project=self.project, author=self.card.user, author_name='Ирина',
            author_is_owner=False, text='Хорошо')
        with self.settings(TELEGRAM_CHAT_ID='999'):
            with mock.patch.object(tg_service, 'send_to', return_value=True) as sent:
                notify.new_message(from_client)
        self.assertEqual(sent.call_args[0][0], '999')  # исполнителю

    def test_long_message_is_cut_not_dumped_whole(self):
        long = Message.objects.create(
            project=self.project, author=self.owner, author_name='Антон',
            author_is_owner=True, text='а' * 500)
        with mock.patch.object(tg_service, 'send_to', return_value=True) as sent:
            notify.new_message(long)
        self.assertLess(len(sent.call_args[0][1]), 500)
        self.assertIn('…', sent.call_args[0][1])

    def test_a_stage_that_did_not_move_sends_nothing(self):
        """Уведомление о нажатии на тот же статус учит не читать
        уведомления."""
        self.stage.mark(Stage.Status.RUNNING)
        self.client.force_login(self.owner)
        with mock.patch.object(notify, 'stage_moved') as told:
            self.client.post(
                reverse('cabinet_stage_status', args=[self.stage.pk]),
                {'status': Stage.Status.RUNNING},
                headers={'x-requested-with': 'XMLHttpRequest'})
        told.assert_not_called()


class PasswordResetTests(TestCase):
    """Пароль от кабинета, куда заходят раз в неделю, будет забыт."""

    def setUp(self):
        self.card = Client.objects.create(name='Ирина', phone='+79130000040',
                                          email='irina@example.com')
        access.issue(self.card)
        self.card.refresh_from_db()

    def test_link_is_offered_only_when_mail_can_be_sent(self):
        """Кнопка, ведущая в тупик, хуже её отсутствия: человек нажмёт,
        ничего не получит и решит, что сломался весь кабинет."""
        with self.settings(EMAIL_READY=True):
            self.assertIn('Забыли пароль',
                          self.client.get(reverse('login')).content.decode())
        with self.settings(EMAIL_READY=False):
            self.assertNotIn('Забыли пароль',
                             self.client.get(reverse('login')).content.decode())

    def test_page_says_the_truth_when_mail_is_not_set_up(self):
        with self.settings(EMAIL_READY=False):
            body = self.client.get(reverse('password_reset')).content.decode()
        self.assertIn('не подключена', body)
        self.assertNotIn('name="email"', body)

    def test_letter_carries_a_working_link(self):
        with self.settings(EMAIL_READY=True):
            self.client.post(reverse('password_reset'),
                             {'email': 'irina@example.com'})
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn(self.card.user.username, body)

        link = re.search(r'https?://\S+/cabinet/parol/novyy/\S+', body)
        self.assertIsNotNone(link, 'в письме нет ссылки на смену пароля')
        path = '/' + link.group(0).split('/', 3)[3]
        self.assertEqual(self.client.get(path, follow=True).status_code, 200)

    def test_unknown_address_is_answered_the_same_way(self):
        """Отвечать «такого адреса нет» значит позволить перебором
        узнать, кто у меня в клиентах."""
        with self.settings(EMAIL_READY=True):
            response = self.client.post(reverse('password_reset'),
                                        {'email': 'nobody@example.com'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)


class WorkPageTests(TestCase):
    """Страницы отдельных работ и короткие карточки на главной."""

    def test_every_work_has_its_own_page(self):
        for item in WORKS:
            with self.subTest(work=item['slug']):
                response = self.client.get(reverse('work', args=[item['slug']]))
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertIn(item['title'], body)
                self.assertIn(item['site'], body)

    def test_unknown_work_is_not_found(self):
        self.assertEqual(
            self.client.get(reverse('work', args=['net-takoy'])).status_code, 404)

    def test_details_moved_off_the_front_page(self):
        """Раньше всё «было/стало» стояло на главной и растягивало её
        на пять экранов. Теперь подробности живут на своей странице,
        а на главной — вывеска и ссылка."""
        front = self.client.get(reverse('index')).content.decode()
        for item in WORKS:
            with self.subTest(work=item['slug']):
                self.assertIn(reverse('work', args=[item['slug']]), front)
                # Снимков на главной по одному на работу, а не по четыре.
                self.assertNotIn(f'{item["shots"][3]["file"]}-sm.webp', front)

    def test_front_page_keeps_was_and_now_in_a_dialog(self):
        """Окно «было и стало» приезжает вместе со страницей: два коротких
        списка дешевле привезти сразу, чем идти за ними запросом."""
        front = self.client.get(reverse('index')).content.decode()
        for item in WORKS:
            with self.subTest(work=item['slug']):
                self.assertIn(f'id="ba-{item["slug"]}"', front)
                self.assertIn(item['was'][0][:40], front)

    def test_work_pages_are_in_the_sitemap(self):
        body = self.client.get(reverse('sitemap')).content.decode()
        for item in WORKS:
            self.assertIn(f'raboty/{item["slug"]}/', body)


class CabinetEntranceTests(TestCase):
    """Вход в кабинет должен быть findable — его на сайте не было вовсе."""

    def test_footer_links_to_the_cabinet(self):
        for name in ('index', 'club', 'constructor', 'privacy'):
            with self.subTest(page=name):
                body = self.client.get(reverse(name)).content.decode()
                self.assertIn(reverse('cabinet'), body)

    def test_cabinet_sends_a_stranger_to_the_login_page(self):
        response = self.client.get(reverse('cabinet'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)
