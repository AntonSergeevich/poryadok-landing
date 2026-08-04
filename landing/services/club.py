"""Выдача доступа в закрытый клуб после оплаты.

Вынесено из views отдельным модулем не ради красоты: этой же цепочкой
пользуется команда `settle_payment`, когда уведомление об оплате
потерялось. Единственный экземпляр логики — единственное место, где
её можно сломать.
"""
import logging

from . import telegram as tg

logger = logging.getLogger(__name__)


def grant_access(payment):
    """Включает подписку и выдаёт одноразовую ссылку в канал."""
    subscription = getattr(payment, 'club_subscription', None)
    if subscription is None:
        logger.warning('Оплата %s не привязана к подписке — доступ не выдан', payment.pk)
        return None
    subscription.activate()

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
