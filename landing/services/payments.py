"""Приём оплат через ЮKassa.

Модуль включается сам, когда в .env заданы YOOKASSA_SHOP_ID и
YOOKASSA_SECRET_KEY. Пока их нет, сайт работает в режиме заявок:
страница клуба просто собирает контакт, а оплату вы проводите вручную
и отмечаете в CRM. Ничего переписывать при подключении эквайринга
не придётся — включится этот код.
"""
import logging
import uuid

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API = 'https://api.yookassa.ru/v3/payments'
TIMEOUT = 15


def is_enabled():
    return bool(getattr(settings, 'YOOKASSA_SHOP_ID', None)
                and getattr(settings, 'YOOKASSA_SECRET_KEY', None))


def create_payment(amount, description, return_url, metadata=None):
    """Создаёт платёж и возвращает (payment_id, confirmation_url).

    При любой ошибке возвращает (None, None) — вызывающий код показывает
    посетителю запасной путь, а не пустой экран.
    """
    if not is_enabled():
        logger.info('ЮKassa не настроена — платёж не создан')
        return None, None

    payload = {
        'amount': {'value': f'{amount:.2f}', 'currency': 'RUB'},
        'capture': True,
        'confirmation': {'type': 'redirect', 'return_url': return_url},
        'description': description[:128],
        'metadata': metadata or {},
    }
    try:
        response = requests.post(
            API,
            json=payload,
            auth=(settings.YOOKASSA_SHOP_ID, settings.YOOKASSA_SECRET_KEY),
            headers={'Idempotence-Key': str(uuid.uuid4())},
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException:
        logger.exception('ЮKassa недоступна при создании платежа')
        return None, None

    if response.status_code not in (200, 201):
        logger.error('ЮKassa вернула HTTP %s: %s', response.status_code, response.text[:400])
        return None, None

    data = response.json()
    return data.get('id'), (data.get('confirmation') or {}).get('confirmation_url')


def fetch_payment(payment_id):
    """Спрашивает у ЮKassa текущий статус платежа.

    Нужен, чтобы не верить телу вебхука на слово: вебхук лишь сигнал
    «посмотри сюда», а источник правды — ответ API.
    """
    if not is_enabled() or not payment_id:
        return None
    try:
        response = requests.get(
            f'{API}/{payment_id}',
            auth=(settings.YOOKASSA_SHOP_ID, settings.YOOKASSA_SECRET_KEY),
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException:
        logger.exception('ЮKassa недоступна при проверке платежа %s', payment_id)
        return None
    if response.status_code != 200:
        logger.error('ЮKassa: статус платежа %s — HTTP %s', payment_id, response.status_code)
        return None
    return response.json()
