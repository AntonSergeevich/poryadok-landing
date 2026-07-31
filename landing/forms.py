"""Формы сайта. Вся проверка ввода — на сервере; маска в браузере лишь помогает."""
import re

from django import forms

from .models import Lead

CONSENT_ERROR = 'Без согласия на обработку данных я не смогу вам позвонить.'


class PhoneMixin:
    def clean_phone(self):
        raw = self.cleaned_data.get('phone', '')
        digits = re.sub(r'\D', '', raw)
        if len(digits) == 11 and digits[0] in '78':
            digits = digits[1:]
        if len(digits) != 10:
            raise forms.ValidationError('Введите номер целиком — 10 цифр после +7.')
        return '+7' + digits


class LeadForm(PhoneMixin, forms.ModelForm):
    """Заявка на бесплатный разбор."""

    consent = forms.BooleanField(required=True, error_messages={'required': CONSENT_ERROR})

    class Meta:
        model = Lead
        fields = ('name', 'phone', 'area')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = True
        self.fields['name'].error_messages['required'] = 'Как к вам обращаться?'
        self.fields['area'].required = False

    def clean_name(self):
        name = self.cleaned_data.get('name', '').strip()
        if len(name) < 2:
            raise forms.ValidationError('Как к вам обращаться?')
        return name


class ClubForm(PhoneMixin, forms.ModelForm):
    """Заявка в закрытый клуб. Telegram обязателен — без него некуда выдавать доступ."""

    consent = forms.BooleanField(required=True, error_messages={'required': CONSENT_ERROR})
    plan = forms.ChoiceField(required=False, choices=(
        ('month', 'Месяц'), ('quarter', 'Три месяца'), ('year', 'Год'),
    ))

    class Meta:
        model = Lead
        fields = ('name', 'phone', 'telegram_username', 'area')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = True
        self.fields['name'].error_messages['required'] = 'Как вас зовут?'
        self.fields['telegram_username'].required = True
        self.fields['telegram_username'].error_messages['required'] = (
            'Нужен ник в Telegram — иначе доступ выдать некуда.')

    def clean_telegram_username(self):
        value = self.cleaned_data.get('telegram_username', '').strip().lstrip('@')
        if not re.fullmatch(r'[A-Za-z0-9_]{4,32}', value):
            raise forms.ValidationError(
                'Ник в Telegram — латиница, цифры и подчёркивание, от 4 символов.')
        return value


class AuditForm(PhoneMixin, forms.Form):
    """Экспресс-проверка выгрузки продаж."""

    ALLOWED = ('.xlsx', '.xls', '.csv')
    MAX_BYTES = 5 * 1024 * 1024

    phone = forms.CharField(max_length=32)
    sales_file = forms.FileField()
    consent = forms.BooleanField(required=True, error_messages={'required': CONSENT_ERROR})

    def clean_sales_file(self):
        uploaded = self.cleaned_data['sales_file']
        name = (uploaded.name or '').lower()
        if not name.endswith(self.ALLOWED):
            raise forms.ValidationError('Нужен файл .xlsx, .xls или .csv.')
        if uploaded.size > self.MAX_BYTES:
            raise forms.ValidationError('Файл больше 5 МБ — пришлите выгрузку поменьше.')
        return uploaded
