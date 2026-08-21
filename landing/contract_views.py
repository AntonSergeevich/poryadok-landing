"""Договор в кабинете: собрать, выставить, распечатать, подписать.

Почему PDF собирается печатью браузера, а не библиотекой
-------------------------------------------------------

Договор — это одна страница разметки, и она уже есть: её читают
на экране. Библиотека для PDF означала бы вторую вёрстку того же
документа, и через полгода они разошлись бы — на экране один текст,
в файле другой. Для договора это худший из возможных дефектов.

Печать браузера даёт настоящий PDF («Сохранить как PDF» в окне печати),
одинаковый с тем, что человек видел, и умеет это любой браузер и любой
телефон. Взамен — точный контроль полей через @page и @media print.

Кто что может
-------------

Выставляет и отменяет исполнитель. Заказчик видит выставленный договор,
печатает его и загружает подписанный скан — это и есть подпись
по п. 10.1 самого договора. Черновик заказчику не показывается вовсе.
"""
import logging

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required

from . import contract as text
from .cabinet import (_may_touch, _parse_money, is_owner, owner_only,
                      wants_json)
from .models import Contract, Project
from .services import notify, papers

logger = logging.getLogger(__name__)

# Скан договора. Больше двадцати мегабайт — это не скан, а фотография
# каждой страницы телефоном на максимальном качестве; такие присылают,
# и такие же не открываются потом ни в одной бухгалтерии.
MAX_SCAN = 20 * 1024 * 1024
SCAN_TYPES = ('.pdf', '.jpg', '.jpeg', '.png', '.heic', '.tif', '.tiff')


@owner_only
@require_POST
def contract_create(request, pk):
    """Собрать черновик договора по проекту."""
    project = get_object_or_404(
        Project.objects.select_related('client').prefetch_related('stages'),
        pk=pk)

    if project.contract is not None:
        messages.info(request, 'Договор по этому проекту уже есть.')
        return redirect('cabinet_contract', pk=project.contract.pk)

    contract = papers.draft(
        project,
        amount=_parse_money(request.POST.get('amount')),
        prepay_percent=_percent(request.POST.get('prepay_percent')),
        system_name=(request.POST.get('system_name') or '').strip() or None,
    )
    messages.success(
        request,
        'Черновик собран. Перечитайте и выставьте на подписание.')
    return redirect('cabinet_contract', pk=contract.pk)


@login_required
def contract_page(request, pk):
    """Договор целиком — тот же документ, что уйдёт на печать."""
    contract = _contract_for(request, pk)
    data, body, lead = papers.view_data(contract)

    return render(request, 'landing/cabinet/contract.html', {
        'section': 'clients',
        'contract': contract,
        'project': contract.project,
        'data': data,
        'body': body,
        'preamble': lead,
        'owner_view': is_owner(request.user),
        # Незаполненные реквизиты показываются до выставления, а не после:
        # договор без счёта заказчик получит, распечатает, подпишет —
        # и только тогда спросит, куда платить.
        'gaps': text.missing_requisites() if is_owner(request.user) else [],
        'notes': text.LAWYER_NOTES if is_owner(request.user) else (),
        'max_mb': MAX_SCAN // (1024 * 1024),
    })


@owner_only
@require_POST
def contract_update(request, pk):
    """Правка черновика. Выставленный договор не правится вовсе."""
    contract = get_object_or_404(
        Contract.objects.select_related('project__client'), pk=pk)
    if contract.status != Contract.Status.DRAFT:
        return _fail(request, contract,
                     'Выставленный договор не правится. Отмените его '
                     'и соберите новый — так у сторон не разойдутся тексты.')

    amount = _parse_money(request.POST.get('amount'))
    if amount is not None and amount >= 0:
        contract.amount = amount
    percent = _percent(request.POST.get('prepay_percent'))
    if percent is not None:
        contract.prepay_percent = percent
    for field, limit in (('system_name', 200), ('number', 32)):
        if field in request.POST:
            value = (request.POST.get(field) or '').strip()[:limit]
            if value:
                setattr(contract, field, value)
    for field in ('term_days', 'warranty_days', 'support_days'):
        value = _days(request.POST.get(field))
        if value is not None:
            setattr(contract, field, value)
    if 'scope' in request.POST:
        contract.scope = (request.POST.get('scope') or '').strip()

    contract.save()
    return _ok(request, contract, 'Сохранил.')


@owner_only
@require_POST
def client_requisites(request, pk):
    """Реквизиты заказчика для договора.

    Правятся со страницы договора, а не из карточки клиента: спрашивают
    их ровно один раз — когда собирают документ, — и отправлять за этим
    в другой раздел значит гарантированно получить договор с прочерками.
    """
    contract = get_object_or_404(
        Contract.objects.select_related('project__client'), pk=pk)
    client = contract.project.client

    for field, limit in (('legal_name', 250), ('inn', 12),
                         ('address', 250), ('signer', 160), ('email', 254)):
        if field in request.POST:
            setattr(client, field, (request.POST.get(field) or '').strip()[:limit])
    client.save()

    if contract.status == Contract.Status.DRAFT:
        return _ok(request, contract, 'Реквизиты сохранил.')
    # У выставленного договора реквизиты уже в снимке, и менять их
    # там нельзя. Карточку клиента поправили — следующий договор
    # соберётся с новыми данными, этот останется таким, каким его
    # видел заказчик.
    return _ok(request, contract,
               'Сохранил в карточке. В этом договоре останутся прежние — '
               'он уже выставлен.')


@owner_only
@require_POST
def contract_issue(request, pk):
    """Выставить на подписание. С этого момента текст заморожен."""
    contract = get_object_or_404(
        Contract.objects.select_related('project__client')
        .prefetch_related('project__stages'), pk=pk)

    gaps = text.missing_requisites()
    if gaps:
        return _fail(request, contract,
                     'Не заполнены реквизиты: ' + ', '.join(gaps) +
                     '. Без них платить некуда.')

    if not papers.issue(contract):
        return _fail(request, contract, 'Этот договор уже выставлен.')

    notify.contract_issued(contract)
    return _ok(request, contract,
               'Договор выставлен — заказчик видит его в своём кабинете.',
               forms=True)


@owner_only
@require_POST
def contract_cancel(request, pk):
    """Отменить договор.

    Удаления здесь нет намеренно. Даже отменённый договор — это то, что
    видел заказчик, и стирать его значит терять ответ на вопрос
    «а что мне присылали в прошлый вторник».
    """
    contract = get_object_or_404(
        Contract.objects.select_related('project'), pk=pk)
    if contract.status == Contract.Status.SIGNED:
        return _fail(request, contract,
                     'Подписанный договор не отменяется здесь. '
                     'Расторжение оформляется соглашением сторон.')
    contract.status = Contract.Status.CANCELED
    contract.save(update_fields=['status', 'updated_at'])
    messages.success(request, 'Договор отменён.')
    return redirect('cabinet_project', pk=contract.project_id)


@login_required
@require_POST
def contract_sign(request, pk):
    """Загрузить подписанный скан.

    Загружает обычно заказчик, но и исполнителю это разрешено: половина
    заказчиков присылает скан в мессенджер, и заставлять их заходить
    в кабинет ради загрузки — работа ради формы.
    """
    contract = _contract_for(request, pk)
    if contract.status not in (Contract.Status.ISSUED, Contract.Status.SIGNED):
        return _fail(request, contract, 'Этот договор ещё не выставлен.')

    uploaded = request.FILES.get('scan')
    if uploaded is None:
        return _fail(request, contract, 'Файл не приложен.')
    if uploaded.size > MAX_SCAN:
        return _fail(request, contract,
                     f'Файл больше {MAX_SCAN // (1024 * 1024)} МБ. '
                     'Обычно помогает «Сохранить как PDF» вместо фотографий.')
    if not uploaded.name.lower().endswith(SCAN_TYPES):
        return _fail(request, contract,
                     'Принимаю PDF и фотографии. Документ Word подписью '
                     'не является.')

    papers.sign(contract, uploaded, uploaded.name)
    if not is_owner(request.user):
        notify.contract_signed(contract)
    return _ok(request, contract, 'Подписанный договор принят.')


# ── Общее ────────────────────────────────────────────────────────────

def _contract_for(request, pk):
    """Договор, который этому человеку можно видеть.

    Проверка на сервере, а не в вёрстке: заказчик, подставивший в адрес
    чужой номер, обязан получить «не найдено», а не чужие суммы.
    """
    contract = get_object_or_404(
        Contract.objects.select_related('project__client'), pk=pk)
    if not _may_touch(request.user, contract.project):
        raise Http404
    if not is_owner(request.user) and not contract.is_visible_to_client:
        raise Http404
    return contract


def _percent(raw):
    try:
        value = int((raw or '').strip())
    except (TypeError, ValueError):
        return None
    return min(max(value, 0), 100)


def _days(raw):
    try:
        value = int((raw or '').strip())
    except (TypeError, ValueError):
        return None
    return max(value, 0)


def _ok(request, contract, note, forms=False):
    """Ответ на действие: состояние договора и сам документ.

    Документ возвращается вместе с состоянием намеренно. Правка условий
    меняет и суммы прописью, и календарный план, и текст приложений —
    обновить панель, забыв про лист, значит показать экран, где сверху
    новая цена, а в договоре ниже старая.
    """
    if not wants_json(request):
        messages.success(request, note)
        return redirect('cabinet_contract', pk=contract.pk)

    contract.refresh_from_db()
    data, body, lead = papers.view_data(contract)
    also = {
        '[data-doc-slot]': render_to_string(
            'landing/cabinet/_contract_doc.html',
            {'contract': contract, 'project': contract.project,
             'data': data, 'body': body, 'preamble': lead},
            request=request),
    }
    if forms:
        # Статус сменился — значит формы правки больше не действуют.
        # Оставить их на экране значит обещать кнопкой то, чего она
        # уже не сделает.
        also['[data-draft-slot]'] = render_to_string(
            'landing/cabinet/_contract_draft.html',
            {'contract': contract, 'project': contract.project,
             'owner_view': True, 'gaps': text.missing_requisites()},
            request=request)

    return JsonResponse({
        'ok': True,
        'note': note,
        'html': render_to_string('landing/cabinet/_contract_state.html', {
            'contract': contract,
            'owner_view': is_owner(request.user),
            'max_mb': MAX_SCAN // (1024 * 1024),
        }, request=request),
        'also': also,
    })


def _fail(request, contract, note):
    if not wants_json(request):
        messages.error(request, note)
        return redirect('cabinet_contract', pk=contract.pk)
    return JsonResponse({'ok': False, 'error': note}, status=400)
