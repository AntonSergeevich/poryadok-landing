"""Проверки, которые ловят поломки до того, как их увидят клиенты.

Запуск: python manage.py test landing
"""
import re
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from .forms import LeadForm
from .models import (Client, ClubSubscription, Lead, Payment,
                     format_phone, normalize_phone)

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

    def test_audit_result_redirects_when_empty(self):
        self.assertEqual(self.client.get(reverse('audit_result')).status_code, 302)

    def test_webhook_rejects_garbage(self):
        response = self.client.post(reverse('yookassa_webhook'),
                                    data='не json', content_type='application/json')
        self.assertEqual(response.status_code, 400)
