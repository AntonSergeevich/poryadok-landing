"""Переписка по проекту.

Здесь вся работа с сообщениями, чтобы кабинет исполнителя и кабинет
заказчика делали её одним кодом. Две реализации одной переписки — это
однажды две разные истории одного проекта, и доказывать по ним нельзя
ничего.

Про скорость. Она здесь не «приятное дополнение», а условие: в чат,
который думает секунду на каждое сообщение, просто перестают писать
и возвращаются в мессенджер. Поэтому:

* **сообщение показывается сразу**, не дожидаясь сервера, — подтверждение
  приходит следом и лишь помечает его отправленным;
* **новые дозагружаются по одному запросу** «что появилось после N-го»,
  а не перезагрузкой всей ленты;
* **лента не тянет всю историю**: показываются последние, остальное
  подгружается кнопкой.
"""
import logging

from django.conf import settings

from ..models import Message, MessageFile

logger = logging.getLogger(__name__)

# Сколько сообщений показывать сразу. Год переписки в одном запросе —
# это секунда ожидания на открытии кабинета и мегабайт трафика с телефона.
PAGE = 40

# Двадцать мегабайт: макет в PDF и десяток фотографий влезают, видео нет.
MAX_FILE = getattr(settings, 'MAX_UPLOAD_SIZE', 20 * 1024 * 1024)


def visible_name(user, client=None, is_owner=False):
    """Как подписывать сообщение.

    Имя копируется в сообщение при отправке, а не берётся из учётной
    записи при показе. Учётную запись можно переименовать или удалить —
    переписка обязана остаться читаемой.

    Логин в подпись не попадает никогда. «anton» — это то, чем человек
    входит, а не то, как его зовут; заказчик, которому пишет «anton»,
    не понимает, с кем разговаривает.
    """
    full = (user.get_full_name() or '').strip()

    if is_owner:
        return (full or getattr(settings, 'SITE_OWNER', '') or 'Исполнитель')[:120]

    # У заказчика имя берём из карточки, а не из учётной записи. Карточку
    # правит исполнитель, и она всегда свежее: имя в учётной записи —
    # это снимок на момент выдачи доступа, и после «Ирина → Ирина
    # Петровна» оно так и осталось бы прежним во всех новых сообщениях.
    if client is not None and client.name:
        return client.name[:120]
    if full:
        return full[:120]
    return (user.username or 'без имени')[:120]


def post(project, user, text, files=(), is_owner=False, client=None):
    """Записать сообщение. Возвращает его или None, если писать нечего.

    Пустое сообщение без файлов не сохраняется: случайное нажатие
    Enter не должно оставлять в доказательной базе пустые строки.
    """
    text = (text or '').strip()
    kept = [f for f in files if f and f.size <= MAX_FILE]
    if not text and not kept:
        return None

    message = Message.objects.create(
        project=project,
        author=user,
        author_name=visible_name(user, client, is_owner),
        author_is_owner=bool(is_owner),
        text=text,
    )

    for uploaded in kept:
        MessageFile.objects.create(
            message=message,
            file=uploaded,
            name=uploaded.name[:250],
            size=uploaded.size,
        )

    skipped = len(list(files)) - len(kept)
    if skipped:
        logger.warning('Переписка: %s файл(ов) не приняты — больше %s МБ',
                       skipped, MAX_FILE // (1024 * 1024))
    return message


def tail(project, limit=PAGE, before=None):
    """Последние сообщения. `before` — для подгрузки более старых.

    Возвращаются в прямом порядке, как их читают. Разворачивать ленту
    в браузере значит делать это в двух местах.
    """
    rows = project.messages.select_related('author').prefetch_related('files')
    if before:
        rows = rows.filter(pk__lt=before)
    rows = list(rows.order_by('-created_at', '-pk')[:limit])
    rows.reverse()
    return rows


def since(project, after_id):
    """Что появилось после сообщения с таким номером.

    Один короткий запрос вместо перезагрузки ленты. Обычно он возвращает
    пустой список, и это нормально: цена такого запроса — пара сотен байт.
    """
    return list(project.messages
                .filter(pk__gt=after_id)
                .select_related('author')
                .prefetch_related('files')
                .order_by('created_at', 'pk')[:PAGE])


def has_older(project, first_id):
    """Есть ли что подгружать выше. Нужно, чтобы не показывать кнопку
    «показать раньше», за которой ничего нет."""
    if not first_id:
        return False
    return project.messages.filter(pk__lt=first_id).exists()


def mark_read(project, is_owner):
    """Отметить прочитанным то, что написала другая сторона.

    Отметку ставит тот, кто читает, а не тот, кто писал: иначе «прочитано»
    означало бы «доставлено», а это разные вещи, и на них по-разному
    обижаются.
    """
    from django.utils import timezone
    return (project.messages
            .filter(read_at__isnull=True)
            .exclude(author_is_owner=is_owner)
            .update(read_at=timezone.now()))


def read_ids(project, is_owner, among=PAGE):
    """Мои сообщения, которые другая сторона уже прочитала.

    Нужна отдельным списком, потому что «прочитано» меняется без единого
    нового сообщения: собеседник просто открыл кабинет. Без этого списка
    отметка появлялась бы только после перезагрузки страницы — то есть
    практически никогда.

    Смотрим лишь последние сообщения: отметка на прошлогодней переписке
    никому не нужна, а перебирать её на каждый опрос — нет.
    """
    rows = (project.messages
            .filter(author_is_owner=is_owner, read_at__isnull=False)
            .order_by('-pk')
            .values_list('pk', flat=True)[:among])
    return list(rows)


def unread(project, is_owner):
    """Сколько непрочитанного адресовано этой стороне."""
    return (project.messages
            .filter(read_at__isnull=True)
            .exclude(author_is_owner=is_owner)
            .count())
