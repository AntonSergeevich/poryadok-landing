"""Сборка договора: из проекта — в документ.

Здесь одна ответственность: превратить проект в договор и обратно ничего
не менять. Кабинет вызывает `draft()` и `issue()`, всё остальное —
внутренности.

Главное правило записано в модели, но повторю: **выставленный договор
не пересобирается**. `issue()` складывает готовый текст в снимок, и с
этого момента документ живёт сам по себе. Проект после этого можно
переименовать, цену — поправить, текст в contract.py — переписать;
подписанный договор останется прежним. Иначе однажды пришлось бы
объяснять заказчику, почему в его подписанном документе другая сумма.
"""
import logging
from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .. import contract as text
from ..models import Contract

logger = logging.getLogger(__name__)

# Значения по умолчанию. Вынесены сюда, а не разбросаны по форме:
# сроки в договоре должны быть одинаковыми во всех договорах, пока
# я сам не решил иначе.
DEFAULTS = {
    'prepay_percent': 50,
    'warranty_days': 30,
    'support_days': 30,
    'start_days': 3,
    'response_days': 3,
    'pay_days': 5,
    'accept_days': 5,
    'fix_days': 10,
}


def scope_from(project):
    """Состав системы для Приложения № 1.

    Сначала смотрим, что собрано в конструкторе: это и есть состав,
    о котором договаривались, — с названиями блоков и тем, что каждый
    из них закрывает. Состав, названный на словах, и состав, за который
    подписались, обязаны быть одним списком.

    Если конструктором не пользовались, берём этапы: они уже описывают,
    что делается, и второй список того же самого разошёлся бы с первым
    в первую же правку.

    В любом случае это черновик — строки правятся руками перед
    выставлением.
    """
    if project.build_blocks:
        from .. import constructor as build
        rows = []
        for block in (build.by_id(code) for code in project.build_blocks):
            if block:
                rows.append(f'{block["title"]}. {block["gain"]}')
        if rows:
            scale = build.scale_by_id(project.build_scale or 'solo')
            rows.append(f'Масштаб: {scale["title"].lower()}. {scale["note"]}')
            return '\n'.join(rows)

    rows = []
    for stage in project.ordered_stages:
        summary = (stage.summary or '').strip()
        rows.append(f'{stage.title}. {summary}' if summary else stage.title)
    if not rows and project.note:
        rows = [line.strip() for line in project.note.splitlines() if line.strip()]
    return '\n'.join(rows)


def plan_from(project):
    """Календарный план для Приложения № 2: этап, что входит, сколько дней."""
    return [{
        'number': stage.number,
        'title': stage.title,
        'summary': stage.summary,
        'days': stage.planned_days,
    } for stage in project.ordered_stages]


def draft(project, **fields):
    """Завести черновик договора по проекту.

    Черновик, а не сразу «на подписание»: договор перечитывают перед
    отправкой, и промежуточное состояние здесь не бюрократия, а место,
    где ловятся опечатки в сумме.
    """
    when = timezone.localdate()
    values = dict(DEFAULTS)
    values.update({k: v for k, v in fields.items() if v is not None})

    return Contract.objects.create(
        project=project,
        number=fields.get('number') or Contract.next_number(when),
        date=when,
        system_name=(fields.get('system_name') or project.title)[:200],
        amount=Decimal(fields.get('amount') if fields.get('amount') is not None
                       else project.price),
        prepay_percent=values['prepay_percent'],
        term_days=fields.get('term_days') or project.planned_days,
        warranty_days=values['warranty_days'],
        support_days=values['support_days'],
        scope=fields.get('scope') or scope_from(project),
    )


def compose(contract):
    """Собрать данные и текст договора из текущего состояния.

    Отдельно от `issue`, потому что то же самое нужно для показа
    черновика: черновик обязан выглядеть ровно так, как будет выглядеть
    выставленный документ, иначе перечитывать его бессмысленно.
    """
    project = contract.project
    client = project.client

    data = dict(text.executor())
    data.update({
        'number': contract.number,
        'date': text.long_date(contract.date),
        'city': data.get('ex_city') or 'Красноярск',
        'system_name': contract.system_name,

        # Реквизиты заказчика — снимком. Наименование берём из карточки,
        # а если его не заполнили, подставляем имя: договор с пустым
        # местом вместо стороны не подписывают, а имя хотя бы правда.
        'cl_title': client.legal_name or client.name,
        'cl_inn': client.inn,
        'cl_address': client.address,
        'cl_signer': client.signer or client.name,
        'cl_phone': client.phone_pretty,
        'cl_email': client.email,

        'amount_words': text.money(contract.amount),
        'prepay_percent': contract.prepay_percent,
        'prepay_words': text.money(contract.prepay),
        'rest_words': text.money(contract.rest),
        'term_days': contract.term_days,
        'warranty_days': contract.warranty_days,
        'support_days': contract.support_days,
        'cabinet_url': _cabinet_url(),
    })
    data.update({k: v for k, v in DEFAULTS.items() if k not in data})

    # Числа в JSON должны пережить запись и чтение одинаково. Decimal
    # туда не кладётся вовсе, а float в сумме договора — это способ
    # однажды напечатать 189999.99999.
    data['amount'] = f'{contract.amount:.2f}'
    data['prepay'] = f'{contract.prepay:.2f}'
    data['rest'] = f'{contract.rest:.2f}'
    data['plan'] = plan_from(project)
    data['scope'] = [line.strip() for line in (contract.scope or '').splitlines()
                     if line.strip()]

    return data, text.render(data), text.preamble(data)


def issue(contract):
    """Выставить на подписание: собрать снимок и открыть заказчику.

    С этого момента текст договора не пересобирается. Повторный вызов
    ничего не портит, но и снимок не обновляет: выставленный документ
    уже мог уйти на печать.
    """
    if contract.status == Contract.Status.SIGNED:
        return False
    if contract.status == Contract.Status.ISSUED:
        return False

    data, body, lead = compose(contract)
    data['preamble'] = lead
    contract.data = data
    contract.body = body
    contract.status = Contract.Status.ISSUED
    contract.issued_at = timezone.now()
    contract.save(update_fields=['data', 'body', 'status', 'issued_at',
                                 'updated_at'])
    return True


def view_data(contract):
    """Что показывать на странице договора.

    У выставленного берём снимок, у черновика собираем на лету. Разница
    принципиальная: черновик обязан показывать сегодняшнюю цену,
    выставленный — ту, что была в момент выставления.
    """
    if contract.body:
        data = dict(contract.data or {})
        return data, contract.body, data.get('preamble', '')
    return compose(contract)


def sign(contract, uploaded, name=''):
    """Принять подписанный скан от заказчика."""
    contract.signed_file = uploaded
    contract.signed_name = (name or getattr(uploaded, 'name', ''))[:250]
    contract.status = Contract.Status.SIGNED
    contract.signed_at = timezone.now()
    contract.save(update_fields=['signed_file', 'signed_name', 'status',
                                 'signed_at', 'updated_at'])

    # Договор подписан — значит сборка началась. Этап сметы закрывать
    # автоматически я не стал: подпись и согласованная смета совпадают
    # обычно, но не всегда, а закрытый не тем этап потом ищут долго.
    return contract


def _cabinet_url():
    host = getattr(settings, 'SITE_HOST', 's-poryadok.ru')
    return f'https://{host}{reverse("cabinet")}'
