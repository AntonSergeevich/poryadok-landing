"""Контакты и настройки, которые нужны почти в каждом шаблоне."""
from django.conf import settings


def site(request):
    return {
        'SITE_OWNER': settings.SITE_OWNER,
        'SITE_PHONE': settings.SITE_PHONE,
        'SITE_PHONE_PRETTY': settings.SITE_PHONE_PRETTY,
        'SITE_EMAIL': settings.SITE_EMAIL,
        'SITE_CITY': settings.SITE_CITY,
        'SITE_TELEGRAM': settings.SITE_TELEGRAM,
        'SITE_INN': settings.SITE_INN,
        'SITE_REGION': settings.SITE_REGION,
        'SITE_STATUS': settings.SITE_STATUS,
        'YANDEX_METRIKA_ID': settings.YANDEX_METRIKA_ID,
    }
