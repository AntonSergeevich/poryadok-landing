"""Выдача доступа в закрытый клуб после оплаты.

Вынесено из views отдельным модулем не ради красоты: этой же цепочкой
пользуется команда `settle_payment`, когда уведомление об оплате
потерялось. Единственный экземпляр логики — единственное место, где
её можно сломать.
"""
import logging

from django.conf import settings
from django.utils import timezone

from . import telegram as tg

logger = logging.getLogger(__name__)


def club_url():
    """Адрес страницы клуба — им заканчивается любое напоминание."""
    host = getattr(settings, 'SITE_HOST', 's-poryadok.ru')
    return f'https://{host}/club/#join'


def grant_access(payment):
    """Включает подписку и выдаёт одноразовую ссылку в канал."""
    subscription = getattr(payment, 'club_subscription', None)
    if subscription is None:
        logger.warning('Оплата %s не привязана к подписке — доступ не выдан', payment.pk)
        return None

    # Если человек продлевает заранее, оплаченные дни не должны сгорать:
    # новая подписка начинается с конца прежней, а не с сегодня.
    running = subscription.client.club_subscriptions.filter(
        status=subscription.Status.ACTIVE, ends_at__gt=timezone.now()
    ).exclude(pk=subscription.pk).order_by('-ends_at').first()
    subscription.activate(from_moment=running.ends_at if running else None)

    link = tg.create_club_invite(name_hint=subscription.client.name)
    if link:
        subscription.invite_link = link
        subscription.invite_sent_at = subscription.updated_at
        subscription.save(update_fields=['invite_link', 'invite_sent_at', 'updated_at'])
    else:
        logger.error('Не удалось создать приглашение для подписки %s', subscription.pk)

    tg.notify(
        'ПОРЯДОК // ОПЛАЧЕН КЛУБ\n'
        + '-' * 32 + '\n'
        + f'Клиент: {subscription.client.name}\n'
        + f'Телефон: {subscription.client.phone_pretty}\n'
        + f'Telegram: @{subscription.client.telegram_username or "—"}\n'
        + f'Тариф: {subscription.get_plan_display()} · {subscription.price:.0f} ₽\n'
        + f'Доступ до: {subscription.ends_at:%d.%m.%Y}\n'
        + '-' * 32 + '\n'
        + (f'Ссылка-приглашение: {link}' if link
           else 'Ссылку создать не удалось — выдайте доступ вручную.')
    )
    return subscription


def remind(subscription):
    """Предупреждает, что доступ скоро кончится.

    Пишем и участнику, и владельцу. Участнику — потому что человек обычно
    просто забывает, а не отказывается. Владельцу — потому что участнику
    написать удаётся не всегда: без входа через Telegram его числовой id
    неизвестен, и тогда напомнить может только живой человек.
    """
    client = subscription.client
    left = subscription.days_left

    if left == 0:
        when = 'сегодня'
    elif left == 1:
        when = 'завтра'
    else:
        when = f'через {left} дн.'

    sent = tg.send_to(client.telegram_user_id, (
        f'Доступ в клуб «Порядок» заканчивается {when} — '
        f'{subscription.ends_at:%d.%m.%Y}.\n\n'
        'Чтобы остаться, продлите на странице клуба:\n'
        f'{club_url()}\n\n'
        'Если продлевать не планируете — ничего делать не нужно, '
        'доступ закроется сам.'
    ))

    tg.notify(
        'ПОРЯДОК // КЛУБ, СРОК ПОДХОДИТ\n'
        + '-' * 32 + '\n'
        + f'Клиент: {client.name}\n'
        + f'Телефон: {client.phone_pretty}\n'
        + f'Telegram: @{client.telegram_username or "—"}\n'
        + f'Доступ до: {subscription.ends_at:%d.%m.%Y} ({when})\n'
        + '-' * 32 + '\n'
        + ('Участнику написал.' if sent
           else 'Участнику написать НЕ СМОГ — telegram ID неизвестен, '
                'напомните сами.')
    )

    subscription.reminded_at = timezone.now()
    subscription.save(update_fields=['reminded_at', 'updated_at'])
    return sent


def farewell(subscription, removed):
    """Сообщает участнику, что доступ закрыт, и как вернуться."""
    return tg.send_to(subscription.client.telegram_user_id, (
        'Доступ в клуб «Порядок» закончился '
        f'{subscription.ends_at:%d.%m.%Y}.\n\n'
        + ('Из канала вы вышли автоматически.\n\n' if removed else '')
        + 'Вернуться можно в любой момент — ссылка на вступление придёт '
        'сразу после оплаты:\n'
        f'{club_url()}'
    ))
