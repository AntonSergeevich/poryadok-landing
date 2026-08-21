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
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.contrib.auth.forms import PasswordChangeForm
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from .models import (Client, Lead, Message, Payment, Project, Stage,
                     StageTask)
from .services import chat
from .services import notify
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
    """Пускать только исполнителя.

    Три случая, и все три разные:

    * не вошёл — на страницу входа. Раньше здесь был 404, и человек,
      нажавший «Заявки» из закладки, видел «страница не найдена»
      вместо формы входа;
    * вошёл заказчиком — в его собственный кабинет: он туда и шёл;
    * вошёл, но ни то ни другое — 404, потому что такого раздела для
      него действительно нет.
    """
    def guard(request, *args, **kwargs):
        if not is_owner(request.user):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(),
                                         resolve_url(settings.LOGIN_URL))
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
        'section': 'desk',
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
        'purposes': Payment.Purpose.choices,
        **_chat_context(project, owner_view=True, viewer=request.user),
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


# ── Заявки ───────────────────────────────────────────────────────────
#
# Раньше заявку было видно на рабочем столе, но нельзя было ни открыть,
# ни поправить, ни удалить: за этим отправляли в админку. Админка сделана
# для того, кто помнит структуру базы, — там таблицы, а не работа.

@owner_only
def leads(request):
    """Все заявки: сначала те, что ждут действия."""
    rows = Lead.objects.select_related('client').order_by('-created_at')

    which = request.GET.get('show') or 'open'
    if which == 'open':
        rows = rows.exclude(status__in=(Lead.Status.WON, Lead.Status.LOST))
    elif which in Lead.Status.values:
        rows = rows.filter(status=which)

    rows = list(rows[:200])
    # Просроченные — наверх. Заявка, о которой забыли, стоит дороже
    # любой свежей: по ней уже поговорили.
    rows.sort(key=lambda lead: (not lead.is_overdue, ))

    return render(request, 'landing/cabinet/leads.html', {
        'section': 'leads',
        'rows': rows,
        'which': which,
        'statuses': Lead.Status.choices,
        'counts': {
            'open': Lead.objects.exclude(
                status__in=(Lead.Status.WON, Lead.Status.LOST)).count(),
            'overdue': Lead.objects.filter(
                remind_at__lte=timezone.now(), reminded_at__isnull=True,
            ).exclude(status__in=(Lead.Status.WON, Lead.Status.LOST)).count(),
        },
    })


@owner_only
def lead_detail(request, pk):
    """Карточка заявки: всё про неё и следующий шаг."""
    lead = get_object_or_404(Lead.objects.select_related('client'), pk=pk)
    return render(request, 'landing/cabinet/lead.html', {
        'section': 'leads',
        'lead': lead,
        'statuses': Lead.Status.choices,
        # Проект заводится отсюда же: заявка и есть его начало.
        'project': Project.objects.filter(client=lead.client).first()
                   if lead.client_id else None,
    })


@owner_only
@require_POST
def lead_update(request, pk):
    """Правка карточки. Отвечает перерисованной карточкой."""
    lead = get_object_or_404(Lead, pk=pk)

    for field in ('name', 'area', 'telegram_username'):
        if field in request.POST:
            setattr(lead, field, (request.POST.get(field) or '').strip()[:160])
    if 'phone' in request.POST:
        lead.phone = (request.POST.get('phone') or '').strip()[:32]
    if 'note' in request.POST:
        lead.note = (request.POST.get('note') or '').strip()
    if 'remind_at' in request.POST:
        lead.remind_at = _parse_moment(request.POST.get('remind_at'))
        # Новый срок — новое напоминание.
        lead.reminded_at = None

    lead.save()
    return _lead_ok(request, lead, 'Сохранил.')


@owner_only
@require_POST
def lead_status(request, pk):
    """Сменить статус. У статуса есть последствия — их ставит модель."""
    lead = get_object_or_404(Lead, pk=pk)
    status = request.POST.get('status')
    if status not in Lead.Status.values:
        return _lead_fail(request, lead, 'Такого статуса нет.')

    reason = (request.POST.get('lost_reason') or '').strip()
    if status == Lead.Status.LOST and not reason and not lead.lost_reason:
        return _lead_fail(
            request, lead,
            'Напишите причину отказа. Через год она объяснит, что чинить.')

    lead.set_status(status, reason=reason)
    note = {
        Lead.Status.THINKING: f'Напомню через {Lead.THINKING_DAYS} дня.',
        Lead.Status.LOST: 'Записал причину.',
        Lead.Status.WON: 'Теперь можно завести проект.',
    }.get(status, 'Сохранил.')
    return _lead_ok(request, lead, note)


@owner_only
@require_POST
def lead_delete(request, pk):
    """Удалить заявку.

    Здесь удаление настоящее, а не архив: заявка — это ещё не отношения.
    Спам и ошибочные отправления должны исчезать совсем, иначе список
    перестают читать. У проекта, за которым стоят договорённости
    и деньги, всё иначе — его удалять нельзя.
    """
    lead = get_object_or_404(Lead, pk=pk)
    label = str(lead)
    lead.delete()
    messages.success(request, f'Удалил: {label}')
    return redirect('cabinet_leads')


@owner_only
@require_POST
def lead_to_project(request, pk):
    """Завести проект прямо из заявки.

    Одним действием: карточка клиента, проект и восемь этапов. Раньше
    это было три захода в разные разделы, между которыми легко потерять
    и заявку, и настроение.
    """
    lead = get_object_or_404(Lead, pk=pk)
    title = (request.POST.get('title') or '').strip()
    if not title:
        return _lead_fail(request, lead, 'У проекта должно быть название.')

    with transaction.atomic():
        client = lead.client
        if client is None:
            # Ищем по телефону: тот же человек мог оставить заявку дважды.
            client = Client.objects.filter(phone=lead.phone).first()
        if client is None:
            client = Client.objects.create(
                name=lead.name or 'Без имени',
                phone=lead.phone,
                area=lead.area,
                telegram_username=lead.telegram_username,
                note=lead.note,
            )
        lead.client = client
        lead.save(update_fields=['client', 'updated_at'])

        project = Project.objects.create(client=client, title=title[:160])
        project.build_stages()
        lead.set_status(Lead.Status.WON)

    messages.success(request, f'Проект «{project.title}» заведён, этапы разложены.')
    return redirect('cabinet_project', pk=project.pk)


def _parse_moment(raw):
    """«2026-08-25T14:30» из поля формы — в дату со временем.

    Пустое поле означает «не напоминать», а не «напомнить в начале эпохи».
    """
    raw = (raw or '').strip()
    if not raw:
        return None
    moment = parse_datetime(raw)
    if moment is None:
        return None
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return moment


def _lead_ok(request, lead, note):
    if not wants_json(request):
        messages.success(request, note)
        return redirect('cabinet_lead', pk=lead.pk)
    lead.refresh_from_db()
    return JsonResponse({
        'ok': True,
        'note': note,
        'html': render_to_string('landing/cabinet/_lead_card.html',
                                 {'lead': lead,
                                  'statuses': Lead.Status.choices},
                                 request=request),
    })


def _lead_fail(request, lead, text):
    if not wants_json(request):
        messages.error(request, text)
        return redirect('cabinet_lead', pk=lead.pk)
    return JsonResponse({'ok': False, 'error': text}, status=400)


# ── Клиенты ──────────────────────────────────────────────────────────

@owner_only
def clients(request):
    """Список клиентов с проектами и доступом в кабинет."""
    rows = (Client.objects.filter(is_active=True)
            .select_related('user')
            .prefetch_related('projects')
            .order_by('name'))
    return render(request, 'landing/cabinet/clients.html', {
        'section': 'clients',
        'rows': rows,
        'issued': request.session.pop('issued_access', None),
    })


# ── Деньги ───────────────────────────────────────────────────────────

@owner_only
def money(request):
    """Сводка по деньгам: сколько договорились, сколько пришло, что должны."""
    projects = list(Project.objects.select_related('client')
                    .prefetch_related('payments').order_by('-created_at'))

    agreed = sum((p.price for p in projects), Decimal('0'))
    paid = sum((p.paid_total for p in projects), Decimal('0'))

    recent = (Payment.objects.select_related('client', 'project')
              .filter(status=Payment.Status.SUCCEEDED)
              .order_by('-paid_at', '-created_at')[:25])

    return render(request, 'landing/cabinet/money.html', {
        'section': 'money',
        'projects': projects,
        'agreed': agreed,
        'paid': paid,
        'left': agreed - paid,
        'recent': recent,
        'purposes': Payment.Purpose.choices,
    })


@owner_only
@require_POST
def payment_add(request, pk):
    """Записать оплату по проекту.

    Вносится руками: переводы на карту и по счёту — половина всех денег,
    и система, которая их не видит, показывает неправду. Эквайринг живёт
    отдельно и заводит оплату сам.
    """
    project = get_object_or_404(_full_projects(), pk=pk)
    amount = _parse_money(request.POST.get('amount'))
    if amount is None or amount <= 0:
        return _fail(request, project, 'Сумма должна быть числом больше нуля.')

    purpose = request.POST.get('purpose')
    if purpose not in Payment.Purpose.values:
        purpose = Payment.Purpose.PROJECT

    payment = Payment.objects.create(
        client=project.client, project=project,
        amount=amount, purpose=purpose,
        note=(request.POST.get('note') or '')[:200],
        provider='manual',
    )
    payment.mark_succeeded()

    return _money_ok(request, project, f'Записал {amount:.0f} ₽.')


@owner_only
@require_POST
def payment_delete(request, pk):
    """Убрать ошибочную запись об оплате."""
    payment = get_object_or_404(
        Payment.objects.select_related('project__client'), pk=pk)
    project = payment.project
    if project is None:
        payment.delete()
        return redirect('cabinet_money')
    payment.delete()
    project = _full_projects().get(pk=project.pk)
    return _money_ok(request, project, 'Убрал запись.')


@owner_only
@require_POST
def project_price(request, pk):
    """Сумма договорённости по проекту."""
    project = get_object_or_404(_full_projects(), pk=pk)
    amount = _parse_money(request.POST.get('price'))
    if amount is None or amount < 0:
        return _fail(request, project, 'Сумма должна быть числом.')
    project.price = amount
    project.save(update_fields=['price', 'updated_at'])
    project = _full_projects().get(pk=project.pk)
    return _money_ok(request, project, 'Сохранил.')


def _parse_money(raw):
    """«180 000», «180000,50», «180000.50» — в число.

    Пробелы и запятая появляются сами: человек печатает сумму так, как
    привык её читать, а не так, как удобно разбирать.
    """
    raw = (raw or '').replace(' ', '').replace(' ', '').replace(',', '.')
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _money_ok(request, project, note):
    if not wants_json(request):
        messages.success(request, note)
        return redirect('cabinet_project', pk=project.pk)
    return JsonResponse({
        'ok': True,
        'note': note,
        'html': render_to_string('landing/cabinet/_money.html',
                                 {'project': project, 'owner_view': True,
                                  'purposes': Payment.Purpose.choices},
                                 request=request),
    })


# ── Этапы, которых нет ───────────────────────────────────────────────

@owner_only
@require_POST
def stages_build(request, pk):
    """Разложить этапы проекту, заведённому без них.

    Так бывает у проектов из админки и у всех, кто появился до того, как
    этапы вообще завелись. Пустая шкала не отвечает ни на один вопрос,
    ради которых кабинет и делался.
    """
    project = get_object_or_404(Project, pk=pk)
    added = project.build_stages()
    if added:
        messages.success(request, f'Разложил этапов: {added}.')
    else:
        messages.info(request, 'Этапы уже есть — не трогал.')
    return redirect('cabinet_project', pk=project.pk)

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
        'purposes': Payment.Purpose.choices,
        **_chat_context(project, owner_view=False, viewer=request.user),
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

    done = request.POST.get('done') == '1'
    task.toggle(done)

    # Ход заказчика должен быть замечен: он сделал то, чего ждали,
    # и следующий шаг теперь за мной.
    if done and not is_owner(request.user):
        notify.task_done_by_client(task)

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
    task = StageTask.objects.create(stage=stage, title=title[:250], who=who,
                                    order=(last.order + 10) if last else 10)

    # Единственное, ради чего заказчика вообще стоит беспокоить:
    # от него что-то нужно. Остальное он посмотрит сам, когда зайдёт.
    if task.is_client:
        notify.task_for_client(task)

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

    was = stage.status
    with transaction.atomic():
        stage.mark(status)
        waiting = request.POST.get('waiting_on')
        if waiting in Stage.Waiting.values and status != Stage.Status.DONE:
            stage.waiting_on = waiting
            stage.save(update_fields=['waiting_on', 'updated_at'])

    # Сообщаем только о настоящем движении. Нажатие на тот же статус
    # ничего не меняет, а уведомление о нём учит не читать уведомления.
    if was != stage.status:
        notify.stage_moved(stage)

    return _ok(request, stage.project, stage)


# ── Переписка ────────────────────────────────────────────────────────

@login_required
@require_POST
def chat_send(request, pk):
    """Отправить сообщение.

    Отвечает готовой разметкой одного сообщения. Браузер уже показал его
    сам, не дожидаясь ответа, — здесь он лишь заменяет черновик
    настоящим: с номером, временем и ссылками на файлы.
    """
    project = _project_for(request, pk)
    owner = is_owner(request.user)

    message = chat.post(
        project, request.user,
        text=request.POST.get('text'),
        files=request.FILES.getlist('files'),
        is_owner=owner,
        client=None if owner else client_of(request.user),
    )
    if message is None:
        return _chat_fail(request, project, 'Пустое сообщение не отправляю.')

    notify.new_message(message)

    if not wants_json(request):
        return redirect(_chat_url(request, project))
    return JsonResponse({
        'ok': True,
        'id': message.pk,
        'html': _one_message(request, message, owner),
    })


@login_required
@require_POST
def chat_edit(request, pk):
    """Поправить своё сообщение — минуту после отправки.

    Правится только своё и только пока не вышло время. Обе проверки
    здесь, на сервере: кнопка в разметке — это не запрет, а вежливая
    просьба, и обойти её умеет кто угодно.

    Время отправки при этом не меняется, а сообщение помечается как
    поправленное. Переписка нужна как доказательная база, и молчаливая
    подмена задним числом лишает её смысла целиком.
    """
    message = get_object_or_404(
        Message.objects.select_related('project__client'), pk=pk)
    project = message.project
    if not _may_touch(request.user, project):
        raise Http404

    if message.author_id != request.user.pk:
        return _chat_fail(request, project, 'Чужие сообщения не правятся.')
    if not message.can_edit:
        return _chat_fail(
            request, project,
            'Минута прошла. Напишите следующим сообщением — так честнее.')

    text = (request.POST.get('text') or '').strip()
    if not text:
        return _chat_fail(request, project,
                          'Пустым сообщение не станет. Удалять переписку нельзя.')

    message.text = text
    message.edited_at = timezone.now()
    message.save(update_fields=['text', 'edited_at', 'updated_at'])

    owner = is_owner(request.user)
    if not wants_json(request):
        return redirect(_chat_url(request, project))
    return JsonResponse({
        'ok': True,
        'id': message.pk,
        'html': _one_message(request, message, owner),
    })

@login_required
def chat_since(request, pk):
    """Что появилось после сообщения с таким номером.

    Отдельный короткий запрос вместо перезагрузки ленты. Обычно
    возвращает пустой список — и это нормально: он стоит пару сотен байт,
    а чат, который надо обновлять руками, перестают читать.
    """
    project = _project_for(request, pk)
    owner = is_owner(request.user)

    try:
        after = int(request.GET.get('after') or 0)
    except (TypeError, ValueError):
        after = 0

    rows = chat.since(project, after)
    # Отметку о прочтении ставим здесь же: человек, у которого открыт
    # чат, эти сообщения уже видит. Отдельная кнопка «прочитано» —
    # это работа, которую никто не делает.
    chat.mark_read(project, owner)

    return JsonResponse({
        'ok': True,
        'last': rows[-1].pk if rows else after,
        'items': [_one_message(request, row, owner) for row in rows],
        # «Прочитано» меняется без единого нового сообщения: собеседник
        # просто открыл кабинет. Без этого списка отметка появлялась бы
        # только после перезагрузки страницы, то есть почти никогда.
        'read': chat.read_ids(project, owner),
    })


@login_required
def chat_older(request, pk):
    """Подгрузить то, что было раньше показанного."""
    project = _project_for(request, pk)
    owner = is_owner(request.user)

    try:
        before = int(request.GET.get('before') or 0)
    except (TypeError, ValueError):
        before = 0

    rows = chat.tail(project, before=before or None)
    return JsonResponse({
        'ok': True,
        'first': rows[0].pk if rows else before,
        'more': chat.has_older(project, rows[0].pk if rows else before),
        'items': [_one_message(request, row, owner) for row in rows],
    })


def _project_for(request, pk):
    """Проект, к которому у этого человека есть доступ.

    Проверка здесь, на сервере, а не в вёрстке. Заказчик, подставивший
    в адрес чужой номер проекта, должен получить «не найдено», а не
    чужую переписку.
    """
    project = get_object_or_404(Project.objects.select_related('client'), pk=pk)
    if not _may_touch(request.user, project):
        raise Http404
    return project


def _one_message(request, message, owner_view):
    return render_to_string('landing/cabinet/_message.html', {
        'message': message,
        # «Своё» и «с моей стороны» — разные вещи. Справа сообщение
        # стоит потому, что оно с моей стороны; править его можно
        # потому, что написал его именно я.
        'own': message.author_id == request.user.pk,
        # «Своё справа» зависит от того, кто смотрит. Решать это в двух
        # местах — верный способ однажды показать заказчику его же
        # сообщения слева.
        'mine': message.author_is_owner == owner_view,
        'owner_view': owner_view,
    }, request=request)


def _chat_url(request, project):
    if is_owner(request.user):
        return redirect('cabinet_project', pk=project.pk).url + '#chat'
    return redirect('cabinet_mine').url + '#chat'


def _chat_fail(request, project, text):
    if not wants_json(request):
        messages.error(request, text)
        return redirect(_chat_url(request, project))
    return JsonResponse({'ok': False, 'error': text}, status=400)

# ── Общее ────────────────────────────────────────────────────────────

def _full_projects():
    """Проект со всем, что показывает кабинет, — одним заходом в базу.

    Без этого страница с восемью этапами делает девять запросов только
    ради задач, и это тот случай, когда «потом оптимизируем» не наступает.
    """
    return (Project.objects
            .select_related('client')
            .prefetch_related('stages__tasks'))


def _chat_context(project, owner_view, viewer=None):
    """Лента переписки для первой отрисовки.

    `mine` и `own` вешаем на объекты здесь, а не считаем в шаблоне.

    Это две разные вещи, и путать их нельзя. `mine` — «с моей стороны»,
    им решается, справа сообщение или слева. `own` — «написал именно я»,
    им решается право на правку: со стороны заказчика может быть двое,
    и править чужое нельзя даже своему.
    """
    rows = chat.tail(project)
    for row in rows:
        row.mine = (row.author_is_owner == owner_view)
        row.own = viewer is not None and row.author_id == viewer.pk
    chat.mark_read(project, owner_view)
    return {
        'chat_messages': rows,
        'chat_last': rows[-1].pk if rows else 0,
        'chat_first': rows[0].pk if rows else 0,
        'chat_more': chat.has_older(project, rows[0].pk if rows else 0),
    }


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
