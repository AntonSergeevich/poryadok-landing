"""Кабинет: рабочий стол исполнителя и кабинет заказчика.

Два разных экрана поверх одних и тех же данных, и это принципиально.
Исполнитель заходит с вопросом «за что взяться сегодня», заказчик —
с вопросом «что происходит и ждут ли чего-то от меня». Кабинет заказчика
не урезанная копия: урезанная копия отвечала бы на чужой вопрос.

Про асинхронность. Механизмов здесь два, и это не усложнение, а экономия:

* **Переключение этапа ничего не спрашивает у сервера.** Все карточки
  этапов приезжают вместе со страницей и лежат рядом; нажатие на шкале
  показывает нужную. Это мгновенно — а поход на сервер ради того, что
  уже загружено, добавил бы задержку и ничего больше. Без JavaScript
  открыты все карточки: лучше длинно, чем недоступно.

* **Действия уходят запросом и возвращают перерисованный кусок.**
  Отметить задачу, сменить статус, добавить строку — здесь сервер нужен
  по-настоящему. Разметку куска собирает он же, а не браузер: шаблон
  один, и второй его копии на JavaScript не появляется.
"""
import logging

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from .models import Client, Lead, Project, Stage, StageTask
from .services import access

logger = logging.getLogger(__name__)


# ── Кто есть кто ─────────────────────────────────────────────────────

def is_owner(user):
    """Хозяин системы — сотрудник. Отдельной роли заводить незачем:
    исполнитель здесь ровно один, и это тот же человек, что заходит
    в админку."""
    return user.is_authenticated and user.is_staff


def client_of(user):
    """Карточка заказчика по учётной записи, или None."""
    return getattr(user, 'client_card', None)


def owner_only(view):
    """Пускать только исполнителя. Заказчика не выкидываем на страницу
    входа, а уводим в его собственный кабинет: он туда и шёл."""
    def guard(request, *args, **kwargs):
        if not is_owner(request.user):
            if client_of(request.user):
                return redirect('cabinet')
            raise Http404
        return view(request, *args, **kwargs)
    guard.__name__ = view.__name__
    guard.__doc__ = view.__doc__
    return guard


def wants_json(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


# ── Вход ─────────────────────────────────────────────────────────────

@login_required
def home(request):
    """Одна дверь на двоих: развилка по роли, а не два разных адреса.

    Ссылку на кабинет отправляют в мессенджер, и она обязана открываться
    у обоих. Иначе заказчик, которому прислали не тот адрес, упирается
    в «страница не найдена» и звонит.
    """
    if is_owner(request.user):
        return owner_desk(request)
    if client_of(request.user):
        return my_project(request)
    # Учётная запись есть, а привязки нет — обычно это старый доступ,
    # у которого удалили карточку. Молча показывать пустой кабинет хуже,
    # чем сказать правду.
    return render(request, 'landing/cabinet/orphan.html', status=403)


# ── Рабочий стол исполнителя ─────────────────────────────────────────

def owner_desk(request):
    """За что взяться сегодня: свежие заявки и проекты в работе."""
    projects = (Project.objects
                .exclude(status=Project.Status.DONE)
                .select_related('client')
                .prefetch_related('stages__tasks'))
    fresh = Lead.objects.filter(status=Lead.Status.NEW).order_by('-created_at')[:8]

    rows = []
    for project in projects:
        current = project.current_stage
        rows.append({
            'project': project,
            'current': current,
            # «Ход за мной» — единственное, что должно попасть на рабочий
            # стол. Задачи заказчика тут только шумят: сделать их за него
            # всё равно нельзя.
            'mine': current.open_tasks(who=StageTask.Who.ME) if current else [],
            'theirs': current.open_tasks(who=StageTask.Who.CLIENT) if current else [],
        })

    return render(request, 'landing/cabinet/desk.html', {
        'rows': rows,
        'fresh': fresh,
        'clients': Client.objects.filter(is_active=True).order_by('name'),
        'issued': request.session.pop('issued_access', None),
    })


@owner_only
def project_detail(request, pk):
    """Проект целиком: шкала, этапы, задачи, доступ заказчика."""
    project = get_object_or_404(_full_projects(), pk=pk)
    return render(request, 'landing/cabinet/project.html', {
        **_stage_context(project, owner_view=True),
        # Пароль показывается один раз и живёт до первой перерисовки
        # страницы. Держать его дольше негде: в базе только хеш.
        'issued': request.session.pop('issued_access', None),
    })


@owner_only
@require_POST
def project_create(request):
    """Завести проект. Этапы раскладываются сами.

    Спрашивать их формой — верный способ получить проект без этапов:
    восемь строк никто не заполняет, а пустая шкала не отвечает ни на
    один вопрос, ради которых кабинет и делался.

    Клиент приходит полем формы, а не куском адреса: иначе выбор в списке
    пришлось бы превращать в адрес скриптом, и без JavaScript форма
    отправлялась бы в никуда.
    """
    client = get_object_or_404(Client, pk=request.POST.get('client') or 0)
    title = (request.POST.get('title') or '').strip()
    if not title:
        messages.error(request, 'У проекта должно быть название.')
        return redirect('cabinet')

    project = Project.objects.create(client=client, title=title[:160])
    project.build_stages()
    messages.success(request, f'Проект «{project.title}» заведён, этапы разложены.')
    return redirect('cabinet_project', pk=project.pk)


@owner_only
@require_POST
def grant_access(request, client_pk):
    """Завести заказчику кабинет одной кнопкой."""
    client = get_object_or_404(Client, pk=client_pk)
    request.session['issued_access'] = access.issue(client, request)
    back = request.POST.get('back') or ''
    if back.startswith('/'):
        return redirect(back)
    return redirect('cabinet')


@owner_only
@require_POST
def revoke_access(request, client_pk):
    """Закрыть доступ, не трогая историю задач."""
    client = get_object_or_404(Client, pk=client_pk)
    if access.revoke(client):
        messages.success(request, f'Доступ для «{client.name}» закрыт.')
    else:
        messages.info(request, 'Доступа и не было.')
    back = request.POST.get('back') or ''
    if back.startswith('/'):
        return redirect(back)
    return redirect('cabinet')


# ── Кабинет заказчика ────────────────────────────────────────────────

@login_required
def my_project(request):
    """Что происходит и что нужно от вас."""
    client = client_of(request.user)
    if client is None:
        raise Http404

    project = _full_projects().filter(client=client).first()
    if project is None:
        return render(request, 'landing/cabinet/empty.html', {'client': client})

    return render(request, 'landing/cabinet/my_project.html', {
        **_stage_context(project, owner_view=False),
        'client': client,
        'todo': project.client_todo(),
    })


@login_required
def password(request):
    """Сменить пароль. Выданный знает не только его владелец, поэтому
    возможность заменить его должна быть на виду, а не в переписке."""
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        # Без этого смена пароля выкидывает человека из его же сессии.
        update_session_auth_hash(request, user)
        messages.success(request, 'Пароль изменён.')
        return redirect('cabinet')
    return render(request, 'landing/cabinet/password.html', {'form': form})


# ── Действия без перезагрузки ────────────────────────────────────────

@login_required
@require_POST
def task_toggle(request, pk):
    """Отметить задачу сделанной или снять отметку.

    Заказчик закрывает **свои** задачи сам: «прислал фото» — его строка,
    и ждать, пока её закроет исполнитель, значит опять завести переписку
    «а вы получили?». Чужие задачи ему недоступны — не спрятаны в вёрстке,
    а именно недоступны, потому что проверка здесь, на сервере.
    """
    task = get_object_or_404(
        StageTask.objects.select_related('stage__project__client'), pk=pk)
    project = task.stage.project

    if not _may_touch(request.user, project):
        raise Http404
    if not is_owner(request.user) and not task.is_client:
        return _fail(request, project, 'Эту строку закрываю я, не вы.')

    task.toggle(request.POST.get('done') == '1')
    return _ok(request, project, task.stage)


@owner_only
@require_POST
def task_add(request, stage_pk):
    stage = get_object_or_404(
        Stage.objects.select_related('project__client'), pk=stage_pk)
    title = (request.POST.get('title') or '').strip()
    if not title:
        return _fail(request, stage.project, 'Пустую задачу заводить незачем.')

    who = request.POST.get('who') or StageTask.Who.ME
    if who not in StageTask.Who.values:
        who = StageTask.Who.ME

    last = stage.tasks.order_by('-order').first()
    StageTask.objects.create(stage=stage, title=title[:250], who=who,
                             order=(last.order + 10) if last else 10)
    return _ok(request, stage.project, stage)


@owner_only
@require_POST
def task_delete(request, pk):
    task = get_object_or_404(
        StageTask.objects.select_related('stage__project__client'), pk=pk)
    stage = task.stage
    task.delete()
    return _ok(request, stage.project, stage)


@owner_only
@require_POST
def stage_status(request, pk):
    """Сменить статус этапа. Даты проставляются сами."""
    stage = get_object_or_404(
        Stage.objects.select_related('project__client'), pk=pk)
    status = request.POST.get('status')
    if status not in Stage.Status.values:
        return _fail(request, stage.project, 'Такого статуса нет.')

    with transaction.atomic():
        stage.mark(status)
        waiting = request.POST.get('waiting_on')
        if waiting in Stage.Waiting.values and status != Stage.Status.DONE:
            stage.waiting_on = waiting
            stage.save(update_fields=['waiting_on', 'updated_at'])

    return _ok(request, stage.project, stage)


# ── Общее ────────────────────────────────────────────────────────────

def _full_projects():
    """Проект со всем, что показывает кабинет, — одним заходом в базу.

    Без этого страница с восемью этапами делает девять запросов только
    ради задач, и это тот случай, когда «потом оптимизируем» не наступает.
    """
    return (Project.objects
            .select_related('client')
            .prefetch_related('stages__tasks'))


def _stage_context(project, owner_view):
    stages = project.ordered_stages
    current = project.current_stage
    return {
        'project': project,
        'stages': stages,
        'current': current,
        'progress': project.progress,
        'fill_percent': _fill_percent(stages, current),
        # Какая карточка открыта при загрузке. В ответе на действие это
        # поле не ставится: там карточка приходит одна и прятать её нечем
        # и незачем.
        'open_id': current.pk if current else None,
        'owner_view': owner_view,
        'statuses': Stage.Status.choices,
        'whos': StageTask.Who.choices,
    }


def _fill_percent(stages, current):
    """Докуда тянуть заливку линейки — до центра точки текущего этапа.

    Не до «процентов готовности»: заливку сверяют глазом именно с точкой,
    и расхождение в полсантиметра читается как ошибка в системе, а не как
    разница двух способов считать.

    Точки стоят в колонках равной ширины, поэтому центр колонки с номером
    i (считая с нуля) — это (i + ½) от общего числа.
    """
    if not stages:
        return '0'
    if current is None:
        index = len(stages) - 1
    else:
        index = next((i for i, s in enumerate(stages) if s.pk == current.pk), 0)
    # Проект, где закрыто всё, заливаем целиком: половинка последней
    # колонки выглядела бы как «почти сделали».
    if all(s.is_done for s in stages):
        return '100'
    # Строкой, а не числом: при русской локали Django напечатал бы «43,75»,
    # и правило width стало бы недействительным — заливка просто исчезла бы.
    # Ошибку такого рода в вёрстке не видно, её видно только на экране.
    return f'{(index + 0.5) * 100 / len(stages):.2f}'


def _may_touch(user, project):
    if is_owner(user):
        return True
    client = client_of(user)
    return client is not None and client.pk == project.client_id


def _ok(request, project, stage):
    """Ответ на действие: перерисованные карточка этапа и шкала.

    Шкала возвращается вместе с карточкой намеренно. Закрытая задача
    может закрыть этап, закрытый этап двигает подсветку и заливку — и
    обновить одно, забыв про другое, значит показать человеку экран,
    который сам себе противоречит.
    """
    project = _full_projects().get(pk=project.pk)
    fresh = next((s for s in project.ordered_stages if s.pk == stage.pk), stage)
    context = _stage_context(project, owner_view=is_owner(request.user))

    if not wants_json(request):
        target = 'cabinet_project' if is_owner(request.user) else 'cabinet_mine'
        url = (redirect(target, pk=project.pk).url if is_owner(request.user)
               else redirect(target).url)
        return redirect(f'{url}#stage-{fresh.pk}')

    return JsonResponse({
        'ok': True,
        'stage_id': f'stage-{fresh.pk}',
        'stage': render_to_string('landing/cabinet/_stage.html',
                                  {**context, 'stage': fresh}, request=request),
        'rail': render_to_string('landing/cabinet/_rail.html', context, request=request),
        'progress': context['progress'],
    })


def _fail(request, project, text):
    if not wants_json(request):
        messages.error(request, text)
        if is_owner(request.user):
            return redirect('cabinet_project', pk=project.pk)
        return redirect('cabinet_mine')
    return JsonResponse({'ok': False, 'error': text}, status=400)
