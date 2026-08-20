"""Уведомления о движении по проекту.

Этапы ставит исполнитель, заказчик их только видит — и в этом весь смысл
кабинета: чтобы не спрашивать. Но заходить в кабинет за каждым изменением
тоже никто не станет, поэтому кабинет умеет писать сам.

Правило одно: **уведомление уходит той стороне, которой оно адресовано**.
Исполнителю незачем сообщение «вы сменили этап», а заказчику — «заказчик
отметил задачу». Каждое уведомление здесь отвечает на вопрос получателя,
а не пересказывает событие.

Написать заказчику бот может только после того, как тот сам начал с ним
диалог: так устроен Telegram, и обойти это настройками нельзя. Связь
появляется, когда человек делится номером в боте — тогда у клиента
заполняется `telegram_user_id`. Пока он пуст, уведомление молча
пропускается: кабинет от этого работать не перестаёт.

Ни одно уведомление не имеет права уронить действие, которое его вызвало.
Отметить задачу важнее, чем сообщить о ней, — поэтому здесь всё
обёрнуто и любой сбой уходит в журнал.
"""
import logging

from django.conf import settings
from django.urls import reverse

from . import telegram as tg

logger = logging.getLogger(__name__)

# Длинное сообщение в уведомлении не читают — его открывают. Поэтому
# текст режется, а за остальным человек идёт в кабинет.
SNIPPET = 180


def _cabinet_url(path=None):
    host = getattr(settings, 'SITE_HOST', 's-poryadok.ru')
    return f'https://{host}{path or reverse("cabinet")}'


def _safe(what, action):
    """Выполнить и не дать упасть тому, ради чего всё затевалось."""
    try:
        return action()
    except Exception:
        logger.exception('Уведомление «%s» не ушло', what)
        return False


def _to_client(project, text):
    """Написать заказчику, если он связан с ботом."""
    user_id = getattr(project.client, 'telegram_user_id', None)
    if not user_id:
        logger.info('Уведомление заказчику %s пропущено: telegram не привязан',
                    project.client_id)
        return False
    return _safe('заказчику', lambda: tg.send_to(user_id, text))


def _to_owner(text):
    """Написать исполнителю в рабочий чат."""
    chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', None)
    if not chat_id:
        return False
    return _safe('исполнителю', lambda: tg.send_to(chat_id, text))


def stage_moved(stage):
    """Проект перешёл на следующий этап.

    Уходит только заказчику: исполнитель сам это и сделал.
    """
    project = stage.project
    return _to_client(project, '\n'.join([
        f'{project.title}',
        '',
        f'Этап {stage.number} — {stage.title}: {stage.get_status_display().lower()}.',
        f'Готово {project.progress}% пути.',
        '',
        _cabinet_url(),
    ]))


def task_for_client(task):
    """Появилась задача, которая ждёт заказчика.

    Это единственное уведомление, ради которого его вообще стоит
    беспокоить: остальное он посмотрит сам, когда зайдёт.
    """
    project = task.stage.project
    return _to_client(project, '\n'.join([
        f'{project.title}',
        '',
        'От вас нужно:',
        f'• {task.title}',
        '',
        'Отметить сделанным можно прямо в кабинете:',
        _cabinet_url(),
    ]))


def task_done_by_client(task):
    """Заказчик закрыл свою задачу — это ход, и его надо заметить."""
    project = task.stage.project
    return _to_owner('\n'.join([
        'ПОРЯДОК // ЗАКАЗЧИК ОТМЕТИЛ',
        '-' * 32,
        f'{project.client.name} · {project.title}',
        f'Этап {task.stage.number}: {task.title}',
    ]))


def new_message(message):
    """Новое сообщение в переписке — той стороне, которая его не писала."""
    project = message.project
    text = (message.text or '').strip()
    if len(text) > SNIPPET:
        text = text[:SNIPPET].rstrip() + '…'
    if not text:
        count = message.files.count()
        text = f'[файлов: {count}]' if count else '[без текста]'

    if message.author_is_owner:
        return _to_client(project, '\n'.join([
            f'{project.title}',
            '',
            f'{message.author_name}:',
            text,
            '',
            _cabinet_url() + '#chat',
        ]))

    return _to_owner('\n'.join([
        'ПОРЯДОК // СООБЩЕНИЕ ОТ ЗАКАЗЧИКА',
        '-' * 32,
        f'{project.client.name} · {project.title}',
        '',
        f'{message.author_name}:',
        text,
        '',
        _cabinet_url(reverse('cabinet_project', args=[project.pk])) + '#chat',
    ]))
