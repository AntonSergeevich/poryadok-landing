"""Портфолио в кабинете: завести работу, поправить, снять с сайта.

Раньше работа добавлялась правкой landing/works.py, выкладкой
и перезапуском. Пока работ две, это было честнее базы. Дальше — нет:
так работа не добавляется никогда, она откладывается «до следующего
раза, когда буду в коде».

Теперь работа заводится из карточки проекта одной кнопкой: имя, сфера
и город берутся из карточки заказчика, а «было» и «стало» дописываются
руками — их всё равно неоткуда взять, кроме как из разговора.

Про снимки. Их загружают сюда же, и уменьшенных копий не делается:
пережатие ради четырёх картинок на работу означало бы ещё и очередь,
и место, где она однажды встанет. Файлы отдаются как есть, а размер
проверяется при загрузке — так вопрос решается там, где он возникает.
"""
import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .cabinet import owner_only, wants_json
from .models import Project, Work, WorkFact, WorkShot

logger = logging.getLogger(__name__)

# Снимок экрана в 8 МБ — это снимок, сохранённый как PNG без сжатия.
# Такие грузятся минуту и открываются на телефоне ещё дольше.
MAX_SHOT = 8 * 1024 * 1024
SHOT_TYPES = ('.webp', '.png', '.jpg', '.jpeg', '.avif')


@owner_only
def works(request):
    """Все работы: и опубликованные, и снятые."""
    rows = (Work.objects.select_related('project__client')
            .prefetch_related('fact_rows', 'shot_rows'))
    return render(request, 'landing/cabinet/works.html', {
        'section': 'works',
        'rows': rows,
        # Проекты, из которых ещё не сделана работа. Заводить работу
        # проще всего отсюда: половина полей уже заполнена.
        'candidates': (Project.objects.filter(works__isnull=True)
                       .select_related('client').order_by('-created_at')[:20]),
    })


@owner_only
def work_detail(request, pk):
    """Одна работа: всё, что попадёт на страницу."""
    work = get_object_or_404(
        Work.objects.select_related('project__client')
        .prefetch_related('fact_rows', 'shot_rows'), pk=pk)
    return render(request, 'landing/cabinet/work.html', {
        'section': 'works',
        'work': work,
        'max_mb': MAX_SHOT // (1024 * 1024),
    })


@owner_only
@require_POST
def work_create(request):
    """Завести работу. Обычно — из проекта, но можно и без него.

    Без проекта тоже нужно: первые работы сделаны до того, как появились
    проекты в кабинете, и такие ещё будут — не всё, что стоит показать,
    проходило через эту систему.
    """
    project = None
    if request.POST.get('project'):
        project = Project.objects.filter(pk=request.POST['project']).first()

    title = (request.POST.get('title') or '').strip()
    if project is not None and not title:
        title = project.client.name

    if not title:
        messages.error(request, 'У работы должно быть имя — как обращаться '
                                'к человеку.')
        return redirect('cabinet_works')

    work = Work.objects.create(
        project=project,
        slug=_free_slug(request.POST.get('slug') or title),
        title=title[:120],
        role=(request.POST.get('role') or
              (project.client.area if project else ''))[:160],
        city=(project.client.city if project else '')[:120],
        is_published=False,
    )
    messages.success(
        request,
        'Работа заведена и пока не показывается на сайте. Допишите «было» '
        'и «стало» — без них страница ничего не объясняет.')
    return redirect('cabinet_work', pk=work.pk)


@owner_only
@require_POST
def work_update(request, pk):
    """Правка работы. Отвечает перерисованной карточкой."""
    work = get_object_or_404(Work, pk=pk)

    fields = (('title', 120), ('role', 160), ('site', 160), ('city', 120),
              ('term', 60), ('term_note', 250))
    for field, limit in fields:
        if field in request.POST:
            setattr(work, field, (request.POST.get(field) or '').strip()[:limit])
    for field in ('lede', 'was_text', 'now_text'):
        if field in request.POST:
            setattr(work, field, (request.POST.get(field) or '').strip())
    if 'slug' in request.POST:
        asked = (request.POST.get('slug') or '').strip()
        if asked and asked != work.slug:
            work.slug = _free_slug(asked, skip=work.pk)

    work.save()
    return _ok(request, work, 'Сохранил.')


@owner_only
@require_POST
def work_publish(request, pk):
    """Показать работу на сайте или снять с него.

    Работа не публикуется, пока в ней нет «было» и «стало». Пустая
    страница работы — это не «пока без описания», это обещание, за которым
    ничего нет: человек нажал «подробно» и получил заголовок.
    """
    work = get_object_or_404(Work.objects.prefetch_related('shot_rows'), pk=pk)
    show = request.POST.get('show') == '1'

    if show:
        gaps = _not_ready(work)
        if gaps:
            return _fail(request, work,
                         'Пока рано: ' + ', '.join(gaps) + '.')

    work.is_published = show
    work.save(update_fields=['is_published', 'updated_at'])
    return _ok(request, work,
               'Работа на сайте.' if show else 'Снял с сайта.')


@owner_only
@require_POST
def work_delete(request, pk):
    """Удалить работу.

    Настоящее удаление: работа в портфолио — это рассказ, а не запись
    о деньгах. Снять с сайта можно, не удаляя, и обычно это и нужно;
    удаляют то, что завели по ошибке.
    """
    work = get_object_or_404(Work, pk=pk)
    label = work.title
    work.delete()
    messages.success(request, f'Удалил работу «{label}».')
    return redirect('cabinet_works')


@owner_only
@require_POST
def fact_add(request, pk):
    """Добавить проверяемое число.

    «Автотестов — 203» работает не потому, что число большое, а потому,
    что его можно проверить.
    """
    work = get_object_or_404(Work, pk=pk)
    label = (request.POST.get('label') or '').strip()
    value = (request.POST.get('value') or '').strip()
    if not label or not value:
        return _fail(request, work, 'Нужны и подпись, и значение.')

    last = work.fact_rows.order_by('-order').first()
    WorkFact.objects.create(work=work, label=label[:120], value=value[:60],
                            order=(last.order + 10) if last else 10)
    return _ok(request, work, 'Добавил.')


@owner_only
@require_POST
def fact_delete(request, pk):
    fact = get_object_or_404(WorkFact.objects.select_related('work'), pk=pk)
    work = fact.work
    fact.delete()
    return _ok(request, work, 'Убрал.')


@owner_only
@require_POST
def shot_add(request, pk):
    """Загрузить снимок экрана."""
    work = get_object_or_404(Work, pk=pk)
    uploaded = request.FILES.get('image')
    if uploaded is None:
        return _fail(request, work, 'Файл не приложен.')
    if uploaded.size > MAX_SHOT:
        return _fail(request, work,
                     f'Больше {MAX_SHOT // (1024 * 1024)} МБ. Снимок такого '
                     'размера — это PNG без сжатия; сохраните в WebP или JPEG.')
    if not uploaded.name.lower().endswith(SHOT_TYPES):
        return _fail(request, work, 'Картинка: WebP, PNG, JPEG или AVIF.')

    last = work.shot_rows.order_by('-order').first()
    WorkShot.objects.create(
        work=work, image=uploaded,
        caption=(request.POST.get('caption') or '').strip()[:250],
        order=(last.order + 10) if last else 10)
    return _ok(request, work, 'Снимок добавлен.')


@owner_only
@require_POST
def shot_delete(request, pk):
    shot = get_object_or_404(WorkShot.objects.select_related('work'), pk=pk)
    work = shot.work
    shot.delete()
    return _ok(request, work, 'Убрал снимок.')


# ── Общее ────────────────────────────────────────────────────────────

def _not_ready(work):
    """Чего не хватает работе, чтобы её было не стыдно показать."""
    gaps = []
    if not work.was:
        gaps.append('не написано «было»')
    if not work.now:
        gaps.append('не написано «стало»')
    if not work.shot_rows.exists():
        gaps.append('нет ни одного снимка')
    return gaps


def _free_slug(asked, skip=None):
    """Свободный адрес. Занятый дополняется числом, а не отвергается.

    Отказать здесь значит остановить человека посреди дела ради того,
    что система умеет решить сама: два заказчика с именем «Дарья» —
    это не ошибка, это два заказчика.
    """
    base = slugify(asked, allow_unicode=False)[:50] or 'rabota'
    slug = base
    n = 2
    rows = Work.objects.exclude(pk=skip) if skip else Work.objects.all()
    while rows.filter(slug=slug).exists():
        slug = f'{base}-{n}'
        n += 1
    return slug


def _card(request, work):
    work = (Work.objects.select_related('project__client')
            .prefetch_related('fact_rows', 'shot_rows').get(pk=work.pk))
    return render_to_string('landing/cabinet/_work_card.html', {
        'work': work,
        'gaps': _not_ready(work),
        'max_mb': MAX_SHOT // (1024 * 1024),
    }, request=request)


def _ok(request, work, note):
    if not wants_json(request):
        messages.success(request, note)
        return redirect('cabinet_work', pk=work.pk)
    return JsonResponse({'ok': True, 'note': note,
                         'html': _card(request, work)})


def _fail(request, work, note):
    if not wants_json(request):
        messages.error(request, note)
        return redirect('cabinet_work', pk=work.pk)
    return JsonResponse({'ok': False, 'error': note}, status=400)
