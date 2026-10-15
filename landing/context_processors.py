"""Контакты и настройки, которые нужны почти в каждом шаблоне."""
from django.conf import settings

from .build import build_branch, build_id


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
        'TELEGRAM_BOT_USERNAME': settings.TELEGRAM_BOT_USERNAME,
        # По нему страница входа решает, показывать ли «Забыли пароль?».
        'EMAIL_READY': settings.EMAIL_READY,
        # Выложенный коммит — для проверки выкладки снаружи. Читается на
        # каждый запрос, а не один раз при запуске: два файла с диска —
        # дёшево, а значение после выкладки не должно зависеть от того,
        # перезапустился ли процесс.
        'BUILD_ID': build_id(),
        'BUILD_BRANCH': build_branch(),
    }
