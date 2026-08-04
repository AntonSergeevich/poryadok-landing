"""Приём оплат через GetPlatinum.

Включается сам, когда в .env заданы GETPLATINUM_API_KEY и
GETPLATINUM_BASE_URL. Пока их нет, страница клуба работает в режиме
заявок: собирает контакт, а оплату вы проводите вручную.

Как устроена оплата у GetPlatinum
─────────────────────────────────
Обычный путь — два шага: создать заказ (/init-deal), показать человеку
список способов оплаты, затем создать платёж (/init-payment). Мы идём
коротким путём: /init-payment-url сразу возвращает ссылку на готовую
платёжную форму, где человек сам выбирает способ. Для одной услуги с
фиксированной ценой городить свой выбор способов оплаты незачем.

Три вещи, на которых легко ошибиться
────────────────────────────────────
1. **Суммы в копейках.** 3900 ₽ передаётся как 390000. Сумма заказа
   обязана в точности совпадать с суммой позиций, иначе заказ
   не создастся.
2. **Нужен телефон или почта.** Без них не сформировать кассовый чек,
   и заказ будет отклонён.
3. **Уведомлению об оплате верить нельзя.** Подпись мы проверяем, но
   этого мало: сама документация советует переспросить статус. Мы так
   и делаем — подпись отсекает случайных, а /status подтверждает факт.
"""
import hashlib
import hmac
import json
import logging
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT = 20

# Категория позиции для кассового чека: 7 — «Клуб (доступ в закрытое
# сообщество)». Ровно наш случай.
PREFIX_CLUB = 7

# Самозанятый на налоге на профессиональный доход НДС не платит.
VAT_NONE = 'none'


def base_url():
    """Адрес API. Собирается из чего угодно, что похоже на правду.

    В личном кабинете GetPlatinum готового адреса нет — он строится из
    имени аккаунта, а имя видно только в адресной строке кабинета.
    Ошибиться при сборке легко, а ошибка выглядит как «ключ не принят»,
    хотя ключ верный. Поэтому принимаем любой из вариантов:

        poryadok
        poryadok.getplatinum.ru
        https://poryadok.getplatinum.ru
        https://poryadok.getplatinum.ru/api/public/pay

    и приводим к последнему. Если задан GETPLATINUM_ACCOUNT — берём его.
    """
    raw = (getattr(settings, 'GETPLATINUM_BASE_URL', None)
           or getattr(settings, 'GETPLATINUM_ACCOUNT', None) or '').strip()
    if not raw:
        return ''

    raw = raw.rstrip('/')
    if '://' not in raw:
        # Голое имя аккаунта или имя с доменом.
        host = raw if '.' in raw else f'{raw}.getplatinum.ru'
        raw = f'https://{host}'
    if not raw.endswith('/api/public/pay'):
        raw = raw.split('/api/')[0].rstrip('/') + '/api/public/pay'
    return raw


def is_enabled():
    return bool(getattr(settings, 'GETPLATINUM_API_KEY', None) and base_url())


def _call(method, payload):
    """Запрос к API. При любой беде возвращает None, а не исключение."""
    if not is_enabled():
        logger.info('GetPlatinum не настроен — вызов %s пропущен', method)
        return None

    base = base_url()
    try:
        response = requests.post(
            f'{base}/{method}',
            json=payload,
            headers={'Authorization': f'Bearer {settings.GETPLATINUM_API_KEY}'},
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException:
        logger.exception('GetPlatinum недоступен при вызове %s', method)
        return None

    if response.status_code != 200:
        logger.error('GetPlatinum %s: HTTP %s — %s',
                     method, response.status_code, response.text[:400])
        return None

    try:
        data = response.json()
    except ValueError:
        logger.error('GetPlatinum %s: ответ не JSON', method)
        return None

    if data.get('errorCode'):
        logger.error('GetPlatinum %s: ошибка %s — %s',
                     method, data.get('errorCode'), data.get('errorMessage'))
        return None
    return data


def to_kopecks(amount):
    """Рубли в копейки.

    Через Decimal, а не через float: 39.995 в двоичной дроби хранится
    чуть меньше самого себя, и обычное округление даёт 3999 вместо 4000.
    На одном платеже это копейка, но сумма заказа обязана в точности
    совпасть с суммой позиций — иначе заказ вообще не создастся.
    """
    exact = Decimal(str(amount)) * 100
    return int(exact.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def create_payment(*, deal_id, amount, title, client_id,
                   notification_url, success_url, fail_url='',
                   phone='', email='', name='', custom=None):
    """Создаёт платёж и возвращает ссылку на форму оплаты.

    Возвращает (deal_id, form_url) либо (None, None). Вызывающий код при
    неудаче обязан провести человека обычным путём — заявкой, а не
    показывать пустой экран.
    """
    if not (phone or email):
        logger.error('GetPlatinum: нет ни телефона, ни почты — чек не сформировать')
        return None, None

    kopecks = to_kopecks(amount)
    payload = {
        'dealId': str(deal_id)[:255],
        'currency': 'RUB',
        'amount': kopecks,
        # Сумма заказа обязана совпадать с суммой позиций: одна позиция,
        # количество 1, цена равна всей сумме.
        'positions': [{
            'prefix': PREFIX_CLUB,
            'name': title[:128],
            'price': kopecks,
            'quantity': 1,
            'vat': VAT_NONE,
        }],
        'clientParams': {'clientId': str(client_id)[:255]},
        'notificationUrl': notification_url,
        'successUrl': success_url,
    }
    if phone:
        payload['clientParams']['phone'] = phone
    if email:
        payload['clientParams']['email'] = email
    if name:
        payload['clientParams']['name'] = name[:255]
    if fail_url:
        payload['failUrl'] = fail_url
    if custom:
        payload['customParams'] = custom

    data = _call('init-payment-url', payload)
    if not data or not data.get('formUrl'):
        return None, None
    return data.get('dealId') or str(deal_id), data['formUrl']


def fetch_status(deal_id):
    """Спрашивает у GetPlatinum, что на самом деле с платежом."""
    return _call('status', {'dealId': str(deal_id)})


def is_paid(status):
    """Оплачен ли платёж по ответу /status."""
    return bool(status and status.get('isSuccess'))


def checksum(params, api_key=None):
    """Считает контрольную подпись так же, как её считает GetPlatinum.

    Порядок действий задан документацией и важен до мелочи:

    1. ключи сортируются без учёта регистра;
    2. `checksum` и `customParams` из подсчёта исключаются;
    3. строка собирается как `<ключ>;<значение>;`;
    4. `true` и `false` превращаются в `1` и `0`;
    5. вложенные объекты и списки — в JSON;
    6. HMAC-SHA256 ключом API, результат в верхнем регистре.

    ВНИМАНИЕ: пример в документации GetPlatinum не сходится сам с собой —
    показанная строка длиннее заявленной длины, а подпись не совпадает
    ни со строкой из примера, ни с честно отсортированной. Поэтому
    реализован описанный алгоритм, а не подогнанный под пример. Пока
    подпись не подтверждена настоящим уведомлением, она носит
    совещательный характер — см. verify().
    """
    key = api_key or getattr(settings, 'GETPLATINUM_API_KEY', '') or ''
    fields = {k: v for k, v in params.items()
              if k not in ('checksum', 'customParams')}

    parts = []
    for name in sorted(fields, key=str.lower):
        value = fields[name]
        if isinstance(value, bool):
            value = 1 if value else 0
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        elif value is None:
            value = ''
        parts.append(f'{name};{value};')

    return hmac.new(key.encode(), ''.join(parts).encode(),
                    hashlib.sha256).hexdigest().upper()


def verify(params):
    """Сверяет подпись уведомления.

    Возвращает True, если подпись сошлась. Решать судьбу платежа по
    одному этому ответу нельзя, и вот почему.

    Пример подписи в документации GetPlatinum противоречив: заявленная
    длина строки не совпадает с показанной, а контрольная сумма не
    получается ни из строки примера, ни из честно отсортированной.
    Значит воспроизвести их подсчёт «вслепую», до первого настоящего
    уведомления, невозможно.

    Цена ошибки несимметрична. Если наша подпись считается иначе, чем
    у них, и мы отвергаем уведомления, то деньги списаны, а доступ
    не выдан — человек заплатил и остался ни с чем. Если же мы принимаем
    уведомление с неверной подписью, не происходит ничего: сразу после
    этого мы всё равно спрашиваем у GetPlatinum /status, и оплату
    подтверждает только он. Подделать ответ /status нельзя — он приходит
    по нашему ключу API с их сервера.

    Поэтому по умолчанию несовпадение подписи только пишется в журнал,
    а решает /status. Когда подпись подтвердится настоящим уведомлением,
    в .env можно поставить GETPLATINUM_STRICT_CHECKSUM=True, и проверка
    станет обязательной.
    """
    given = (params or {}).get('checksum')
    if not given:
        logger.warning('GetPlatinum: уведомление без подписи')
        return False
    if not getattr(settings, 'GETPLATINUM_API_KEY', None):
        logger.warning('GetPlatinum: ключ не задан, проверить подпись нечем')
        return False

    mine = checksum(params)
    # Сравнение постоянного времени: обычное == подсказывает подбирающему,
    # сколько символов он уже угадал.
    #
    # Сравниваем байты, а не строки: compare_digest со строками падает
    # на любом символе вне ASCII, и прислать в подписи кириллицу мог бы
    # кто угодно — обработчик отвечал бы ошибкой 500 вместо отказа.
    ok = hmac.compare_digest(mine.encode(), str(given).upper().encode())
    if not ok:
        # Свою подпись пишем целиком, чужую — обрезком: в журнал не должны
        # попадать данные, по которым можно что-то подобрать.
        logger.warning('GetPlatinum: подпись не совпала. Наша %s, пришла %s…',
                       mine, str(given)[:12])
    return ok


def checksum_required():
    """Обязательна ли сверка подписи. По умолчанию нет — см. verify()."""
    return bool(getattr(settings, 'GETPLATINUM_STRICT_CHECKSUM', False))
