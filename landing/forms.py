"""Формы сайта. Вся проверка ввода — на сервере; маска в браузере лишь помогает."""
import re

from django import forms

from .models import Lead
from .survey import QUESTIONS

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


class SurveyForm(forms.Form):
    """Разбор процессов.

    Поля строятся из landing/survey.py, а не перечисляются здесь: набор
    вопросов будет меняться, и правка формулировки не должна требовать
    правки формы. Телефон необязателен — человек может пройти разбор
    и не оставлять контакт; результат он всё равно увидит.
    """

    name = forms.CharField(max_length=120, required=False)
    phone = forms.CharField(max_length=32, required=False)
    telegram_username = forms.CharField(max_length=64, required=False)
    consent = forms.BooleanField(required=True, error_messages={'required': CONSENT_ERROR})
    allow_stories = forms.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for q in QUESTIONS:
            self.fields[q['id']] = self._build(q)
            if q.get('other'):
                self.fields[q['id'] + '_other'] = forms.CharField(
                    max_length=300, required=False, strip=True)

    @staticmethod
    def _build(q):
        common = {'required': q.get('required', False), 'label': q['title']}
        common['error_messages'] = {'required': 'Выберите ответ, чтобы идти дальше.'}
        if q['type'] == 'text':
            return forms.CharField(max_length=2000, required=q.get('required', False),
                                   strip=True, label=q['title'],
                                   widget=forms.Textarea)
        choices = [(o['value'], o['label']) for o in q['options']]
        if q.get('other'):
            choices.append(('other', 'Другое'))
        if q['type'] == 'many':
            return forms.MultipleChoiceField(choices=choices, **common)
        return forms.ChoiceField(choices=choices, **common)

    def clean_phone(self):
        raw = (self.cleaned_data.get('phone') or '').strip()
        if not raw:
            return ''
        digits = re.sub(r'\D', '', raw)
        if len(digits) == 11 and digits[0] in '78':
            digits = digits[1:]
        if len(digits) != 10:
            raise forms.ValidationError('Введите номер целиком — 10 цифр после +7.')
        return '+7' + digits

    def clean(self):
        data = super().clean()
        # «Другое» без пояснения — это не ответ.
        for q in QUESTIONS:
            if not q.get('other'):
                continue
            chosen = data.get(q['id'])
            picked_other = ('other' == chosen) or (
                isinstance(chosen, list) and 'other' in chosen)
            if picked_other and not data.get(q['id'] + '_other'):
                self.add_error(q['id'] + '_other', 'Напишите свой вариант.')
        return data

    def answers(self):
        """Только ответы на вопросы, без имени и контактов."""
        out = {}
        for q in QUESTIONS:
            value = self.cleaned_data.get(q['id'])
            if value:
                out[q['id']] = value
            other = self.cleaned_data.get(q['id'] + '_other')
            if other:
                out[q['id'] + '_other'] = other
        return out
