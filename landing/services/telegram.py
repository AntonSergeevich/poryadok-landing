"""Работа с Telegram: уведомления о заявках и доступ в закрытый клуб.

Всё общение с ботом собрано здесь, чтобы views не знали про HTTP.
Любой сбой логируется и возвращается как False — сайт из-за него не падает.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API = 'https://api.telegram.org/bot{token}/{method}'
TIMEOUT = 7


def _call(method, payload):
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
    if not token:
        logger.warning('TELEGRAM_BOT_TOKEN не задан — вызов %s пропущен', method)
        return None
    try:
        response = requests.post(API.format(token=token, method=method),
                                 data=payload, timeout=TIMEOUT)
    except requests.exceptions.RequestException:
        logger.exception('Telegram %s: сеть недоступна', method)
        return None

    try:
        data = response.json()
    except ValueError:
        logger.error('Telegram %s: ответ не JSON (HTTP %s)', method, response.status_code)
        return None

    if not data.get('ok'):
        logger.error('Telegram %s: %s', method, data.get('description'))
        return None
    return data.get('result')


def notify(text):
    """Отправляет текст в рабочий чат. Простым текстом — без разметки:
    в сообщение попадают имена и файлы от посетителей."""
    chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', None)
    if not chat_id:
        logger.warning('TELEGRAM_CHAT_ID не задан — уведомление не отправлено')
        return False
    return _call('sendMessage', {
        'chat_id': chat_id,
        'text': text,
        'disable_web_page_preview': True,
    }) is not None


def notify_lead(lead):
    """Уведомление о новой заявке."""
    lines = [
        'ПОРЯДОК // НОВАЯ ЗАЯВКА',
        '-' * 32,
        f'Источник: {lead.get_source_display()}',
        f'Имя: {lead.name or "—"}',
        f'Телефон: {lead.phone_pretty}',
    ]
    if lead.area:
        lines.append(f'Сфера: {lead.area}')
    if lead.telegram_username:
        lines.append(f'Telegram: @{lead.telegram_username}')
    if lead.comment:
        lines.append(f'Комментарий: {lead.comment}')
    lines += ['-' * 32, 'Заявка сохранена в CRM. Связаться сегодня.']
    return notify('\n'.join(lines))


def notify_survey(entry):
    """Уведомление о пройденном разборе.

    В сообщение идут главные боли и оценка потерь — то, с чего начинать
    разговор. Полные ответы лежат в админке, дублировать их в чат незачем.
    """
    result = entry.diagnose()
    lines = [
        'ПОРЯДОК // РАЗБОР ПРОЦЕССОВ',
        '-' * 32,
        f'Имя: {entry.name or "не назвал"}',
        f'Телефон: {entry.phone_pretty or "не оставил"}',
    ]
    if entry.telegram_username:
        lines.append(f'Telegram: @{entry.telegram_username}')
    if entry.area:
        lines.append(f'Сфера: {entry.area}')

    lines += ['-' * 32, 'Главные боли:']
    if result['top']:
        for i, item in enumerate(result['top'], 1):
            lines.append(f'{i}. {item["title"]} ({item["points"]})')
    else:
        lines.append('— выраженных не набралось')

    money = result.get('estimate')
    if money and money['total_money']:
        lines += [
            '-' * 32,
            f'Оценка потерь: около {money["total_money"]:,.0f} руб. в месяц'.replace(',', ' '),
            f'Рутина: около {money["hours_month"]} часов в месяц',
        ]

    pain = entry.answers.get('pain')
    if pain:
        lines += ['-' * 32, 'Своими словами:', pain[:600]]

    lines += ['-' * 32]
    lines.append('Примеры разрешил' if entry.allow_stories else 'Примеры НЕ разрешил')
    lines.append('Полные ответы — в админке.')
    return notify('\n'.join(lines))


def create_club_invite(name_hint=''):
    """Одноразовая ссылка-приглашение в закрытый канал.

    Ссылка живёт до первого входа (member_limit=1), так что переслать её
    другому человеку бессмысленно. Возвращает URL или None.
    """
    chat_id = getattr(settings, 'TELEGRAM_CLUB_CHAT_ID', None)
    if not chat_id:
        logger.warning('TELEGRAM_CLUB_CHAT_ID не задан — приглашение не создано')
        return None
    result = _call('createChatInviteLink', {
        'chat_id': chat_id,
        'member_limit': 1,
        'name': (name_hint or 'Клуб')[:32],
    })
    return result.get('invite_link') if result else None


def remove_from_club(telegram_user_id):
    """Убирает участника из закрытого канала.

    Сначала бан (он же исключение), сразу следом разбан — иначе человек
    не сможет вернуться по новой ссылке, когда снова оплатит.
    """
    chat_id = getattr(settings, 'TELEGRAM_CLUB_CHAT_ID', None)
    if not chat_id or not telegram_user_id:
        return False
    banned = _call('banChatMember', {'chat_id': chat_id, 'user_id': telegram_user_id})
    if banned is None:
        return False
    _call('unbanChatMember', {'chat_id': chat_id, 'user_id': telegram_user_id,
                              'only_if_banned': True})
    return True
