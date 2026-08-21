"""Файлы к заявке и проекту.

Здесь одна ответственность: принять файл и не принять то, что файлом
притворяется. Проверки собраны в одном месте, потому что грузят файлы
из трёх мест — карточка заявки, карточка проекта и кабинет заказчика, —
и три копии одной проверки означают, что однажды одна из них отстанет.

Про то, что принимается. Список разрешённых расширений, а не список
запрещённых: запрещать приходится всё время что-то новое, а разрешать —
один раз то, что действительно присылают. Здесь это документы, таблицы,
изображения и архивы.

Про безопасность. Файлы отдаются с того же домена, что и кабинет,
и это опасно ровно одним: файл, который браузер решит показать вместо
того, чтобы скачать. Поэтому в nginx раздача /media/ настроена с
`Content-Disposition: attachment` и `X-Content-Type-Options: nosniff` —
что бы ни лежало внутри, оно скачается, а не выполнится. Без этой
настройки список расширений здесь пришлось бы делать намного строже.
"""
import logging

from django.conf import settings

from ..models import Attachment

logger = logging.getLogger(__name__)

MAX_FILE = getattr(settings, 'MAX_UPLOAD_SIZE', 20 * 1024 * 1024)

DOCUMENTS = ('.pdf', '.doc', '.docx', '.rtf', '.odt', '.txt',
             '.xls', '.xlsx', '.csv', '.ods',
             '.ppt', '.pptx', '.odp')
IMAGES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.avif', '.svg')
ARCHIVES = ('.zip', '.7z', '.rar')
# Чертежи и макеты. Их присылают дизайнеры и проектировщики, и «пришлите
# в PDF» здесь — просьба переделать работу.
DESIGN = ('.dwg', '.dxf', '.psd', '.ai', '.fig', '.sketch', '.cdr')

ALLOWED = DOCUMENTS + IMAGES + ARCHIVES + DESIGN


def check(uploaded):
    """Что не так с файлом. Пусто — всё в порядке."""
    if uploaded is None:
        return 'Файл не приложен.'
    if not uploaded.size:
        return 'Файл пустой.'
    if uploaded.size > MAX_FILE:
        return (f'Файл больше {MAX_FILE // (1024 * 1024)} МБ. '
                'Большие макеты обычно присылают ссылкой на облако.')
    if not uploaded.name.lower().endswith(ALLOWED):
        return ('Такие файлы не принимаю. Документы, таблицы, изображения, '
                'чертежи и архивы — да; программы — нет.')
    return ''


def attach(uploaded, lead=None, project=None, note='', from_client=False,
           author_name=''):
    """Положить файл. Возвращает (файл, ошибка).

    Заявка и проект оба необязательны, но хотя бы один нужен: файл,
    не привязанный ни к чему, не показывается нигде и остаётся занимать
    место.
    """
    if lead is None and project is None:
        return None, 'Файл некуда положить.'

    problem = check(uploaded)
    if problem:
        return None, problem

    row = Attachment.objects.create(
        lead=lead, project=project, file=uploaded,
        name=uploaded.name[:250], size=uploaded.size,
        note=(note or '').strip()[:250],
        from_client=bool(from_client),
        author_name=(author_name or '')[:120],
    )
    return row, ''


def carry_over(lead, project):
    """Перенести файлы заявки в проект.

    Заявка становится проектом, и присланные до этого примеры дизайна
    обязаны поехать вместе с ней. Связь с заявкой при этом остаётся:
    откуда файл взялся — тоже часть истории.
    """
    return lead.files.filter(project__isnull=True).update(project=project)


def for_project(project, include_lead=True):
    """Файлы проекта. Вместе с теми, что пришли ещё к заявке."""
    rows = Attachment.objects.filter(project=project)
    if not include_lead:
        rows = rows.filter(lead__isnull=True)
    return rows
