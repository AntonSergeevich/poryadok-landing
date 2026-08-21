"""Конструктор внутри кабинета.

Тот же конструктор, что и на сайте, — те же блоки, та же цена, тот же
код расчёта. Разница в том, кто за ним сидит и что происходит дальше.

На сайте человек собирает систему себе и отправляет заявку. Здесь я
собираю её вместе с ним — по телефону или после разбора — и состав
остаётся в системе: он переезжает из заявки в проект, а из проекта
попадает в Приложение № 1 к договору и в его сумму.

Смысл в том, чтобы состав был **одним списком** на всём пути. Названный
на словах, записанный в заметках и подписанный в договоре состав —
это три разных списка, и расходятся они ровно в том месте, где
начинается спор «мы же договаривались».
"""
import logging

from decimal import Decimal

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import constructor as build
from .cabinet import owner_only
from .models import Lead, Project

logger = logging.getLogger(__name__)


@owner_only
def lead_build(request, pk):
    """Собрать состав по заявке."""
    lead = get_object_or_404(Lead.objects.select_related('client'), pk=pk)
    return _page(request, lead, back=('cabinet_lead', lead.pk),
                 who=lead.name or lead.phone_pretty,
                 action=('cabinet_lead_build', lead.pk))


@owner_only
@require_POST
def lead_build_save(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    picked, scale_id, result = _read(request)
    lead.build_blocks = picked
    lead.build_scale = scale_id
    lead.save(update_fields=['build_blocks', 'build_scale', 'updated_at'])

    messages.success(
        request,
        f'Состав записан: {len(picked)} блоков, ориентир '
        f'{result["total"]:,} ₽.'.replace(',', ' '))
    return redirect('cabinet_lead', pk=lead.pk)


@owner_only
def project_build(request, pk):
    """Собрать состав по проекту."""
    project = get_object_or_404(Project.objects.select_related('client'), pk=pk)
    return _page(request, project, back=('cabinet_project', project.pk),
                 who=project.title,
                 action=('cabinet_project_build', project.pk))


@owner_only
@require_POST
def project_build_save(request, pk):
    """Записать состав и поставить цену.

    Цена ставится сразу, а не «потом руками»: ради неё конструктор
    и открывали, а сумма, набранная в другом поле через полчаса, — это
    ещё одно место, где два числа разойдутся.
    """
    project = get_object_or_404(Project, pk=pk)
    picked, scale_id, result = _read(request)
    project.build_blocks = picked
    project.build_scale = scale_id

    fields = ['build_blocks', 'build_scale', 'updated_at']
    if request.POST.get('set_price') == '1':
        project.price = Decimal(result['total'])
        fields.append('price')
    project.save(update_fields=fields)

    note = f'Состав записан: {len(picked)} блоков.'
    if 'price' in fields:
        note += f' Цена проекта — {result["total"]:,} ₽.'.replace(',', ' ')
        if project.contract is not None:
            # Договор уже мог быть выставлен, и тогда его сумма другая.
            # Молчать об этом нельзя: расхождение всплывёт при оплате.
            note += ' Договор при этом не менялся.'
    messages.success(request, note)
    return redirect('cabinet_project', pk=project.pk)


# ── Общее ────────────────────────────────────────────────────────────

def _read(request):
    """Что пришло из формы и во что это обходится."""
    picked = build.clean(request.POST.getlist('blocks'))
    scale_id = request.POST.get('scale') or 'solo'
    return picked, scale_id, build.estimate(picked, scale_id)


def _page(request, target, back, who, action):
    """Страница конструктора для заявки или проекта.

    Одна на двоих намеренно: это буквально один и тот же экран, и вторая
    его копия разъехалась бы с первой на первой же правке блоков.
    """
    picked = list(target.build_blocks or []) or build.core_ids()
    scale_id = target.build_scale or 'solo'
    result = build.estimate(picked, scale_id)

    return render(request, 'landing/cabinet/build.html', {
        'section': 'leads' if isinstance(target, Lead) else 'clients',
        'target': target,
        'who': who,
        'back_url': back,
        'action_url': action,
        'is_project': isinstance(target, Project),
        'blocks': build.BLOCKS,
        'scales': build.SCALES,
        'picked': result['ids'],
        'scale_id': result['scale']['id'],
        'result': result,
    })
