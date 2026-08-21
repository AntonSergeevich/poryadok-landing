"""Сводка: что происходит в деле целиком.

Зачем она нужна отдельным разделом
----------------------------------

Все числа для неё уже есть в кабинете, но лежат по разным экранам:
заявки — в заявках, деньги — в деньгах, проекты — на рабочем столе.
Вопросы, которые задают себе раз в месяц, ни на один из этих экранов
не помещаются: сколько заработал, откуда приходят те, кто доходит
до договора, и почему отказываются остальные.

Считается на лету, ничего не кэшируется. При двух десятках проектов это
несколько запросов; когда их станет тысяча, здесь появится ночной
пересчёт — но не раньше, потому что кэш, который никто не проверяет,
однажды начинает показывать прошлый год.

Про честность цифр
------------------

Выручка здесь — **полученные** деньги, а не сумма договорённостей.
Разница между ними — это долг, и он показан отдельной строкой. Считать
выручкой подписанное, но не оплаченное, — самый быстрый способ поверить,
что дела идут лучше, чем идут.
"""
from collections import Counter
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from ..models import Client, Contract, Lead, Payment, Project

# Сколько месяцев показывать в столбиках. Год — потому что сезонность
# видна только на годе, а два года на телефоне уже не читаются.
MONTHS_BACK = 12

MONTH_SHORT = ('янв', 'фев', 'мар', 'апр', 'май', 'июн',
               'июл', 'авг', 'сен', 'окт', 'ноя', 'дек')


def money():
    """Деньги: сколько договорились, сколько пришло, сколько должны."""
    projects = Project.objects.all()
    agreed = projects.aggregate(total=Sum('price'))['total'] or Decimal('0')
    paid = (Payment.objects.filter(status=Payment.Status.SUCCEEDED)
            .aggregate(total=Sum('amount'))['total'] or Decimal('0'))

    done = projects.filter(status=Project.Status.DONE).count()
    running = projects.exclude(status__in=(Project.Status.DONE,
                                           Project.Status.FROZEN)).count()

    # Средний чек считаем по проектам с ненулевой ценой: проекты, где
    # цену ещё не проставили, занижали бы его и превращали в неправду.
    priced = [p.price for p in projects if p.price]
    average = sum(priced) / len(priced) if priced else Decimal('0')

    # Переплата — не отрицательный долг. Так бывает, когда аванс внесли
    # до того, как проставили цену; показать это как «должны −40 000»
    # значит один раз напугать, а второй раз — научить не верить числу.
    left = agreed - paid
    return {
        'agreed': agreed,
        'paid': paid,
        'debt': max(left, Decimal('0')),
        'overpaid': max(-left, Decimal('0')),
        'average': average,
        'projects': projects.count(),
        'done': done,
        'running': running,
    }


def by_month(back=MONTHS_BACK):
    """Выручка по месяцам — для столбиков.

    Пустые месяцы остаются в списке. Пропустить их значило бы нарисовать
    ровный рост там, где было три месяца тишины: график без пропусков
    врёт убедительнее любой подписи.
    """
    today = timezone.localdate()
    months = []
    year, month = today.year, today.month
    for _ in range(back):
        months.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    months.reverse()

    rows = (Payment.objects
            .filter(status=Payment.Status.SUCCEEDED, paid_at__isnull=False)
            .values_list('paid_at', 'amount'))
    got = Counter()
    for paid_at, amount in rows:
        local = timezone.localtime(paid_at)
        got[(local.year, local.month)] += amount

    values = [got.get(key, Decimal('0')) for key in months]
    top = max(values) or Decimal('1')
    return [{
        'label': MONTH_SHORT[month - 1],
        'year': year,
        'amount': amount,
        # Высота столбика строкой, а не числом: при русской локали Django
        # напечатал бы «43,75», и правило height стало бы недействительным.
        # Такую ошибку не видно в разметке — её видно только на экране.
        'height': f'{amount * 100 / top:.2f}',
        'is_now': (year, month) == (timezone.localdate().year,
                                    timezone.localdate().month),
    } for (year, month), amount in zip(months, values)]


def leads():
    """Заявки: сколько пришло, сколько дошло до работы, сколько отказов."""
    rows = Lead.objects.all()
    total = rows.count()
    won = rows.filter(status=Lead.Status.WON).count()
    lost = rows.filter(status=Lead.Status.LOST).count()
    open_now = total - won - lost

    # Конверсию считаем от закрытых, а не от всех. Заявки, по которым
    # разговор ещё идёт, — это не отказы, и складывать их в знаменатель
    # значит каждый месяц видеть падающую конверсию просто потому,
    # что заявок стало больше.
    closed = won + lost
    return {
        'total': total,
        'won': won,
        'lost': lost,
        'open': open_now,
        'closed': closed,
        'rate': round(won * 100 / closed) if closed else 0,
    }


def sources():
    """Откуда приходят и кто из них доходит до работы.

    Главное число здесь — не количество, а доля дошедших. Источник,
    дающий двадцать заявок и ноль проектов, стоит дороже, чем кажется:
    он забирает время.
    """
    rows = []
    for value, label in Lead.Source.choices:
        same = Lead.objects.filter(source=value)
        total = same.count()
        if not total:
            continue
        won = same.filter(status=Lead.Status.WON).count()
        lost = same.filter(status=Lead.Status.LOST).count()
        closed = won + lost
        rows.append({
            'label': label,
            'total': total,
            'won': won,
            'rate': round(won * 100 / closed) if closed else None,
        })
    rows.sort(key=lambda row: -row['total'])
    return rows


def refusals(limit=20):
    """Почему отказались. Ради этого списка и заводилось поле причины.

    Одинаковые причины сводятся вместе: три раза «дорого» — это не три
    разных случая, а один повод пересмотреть предложение.
    """
    said = (Lead.objects.filter(status=Lead.Status.LOST)
            .exclude(lost_reason='')
            .order_by('-updated_at')
            .values_list('lost_reason', 'name', 'updated_at'))

    seen = Counter(reason.strip().lower() for reason, _, _ in said)
    rows = []
    used = set()
    for reason, name, when in said:
        key = reason.strip().lower()
        if key in used:
            continue
        used.add(key)
        rows.append({
            'reason': reason,
            'name': name or 'без имени',
            'when': when,
            'times': seen[key],
        })
        if len(rows) >= limit:
            break

    silent = Lead.objects.filter(status=Lead.Status.LOST,
                                 lost_reason='').count()
    return rows, silent


def clients():
    """Заказчики с числами по каждому: проектов, договорились, оплачено."""
    rows = (Client.objects
            .annotate(projects_count=Count('projects', distinct=True))
            .filter(projects_count__gt=0)
            .prefetch_related('projects__payments')
            .order_by('name'))

    out = []
    for card in rows:
        projects = list(card.projects.all())
        agreed = sum((p.price for p in projects), Decimal('0'))
        paid = sum((p.paid_total for p in projects), Decimal('0'))
        out.append({
            'client': card,
            'projects': projects,
            'agreed': agreed,
            'paid': paid,
            'debt': agreed - paid,
            'has_cabinet': bool(card.user_id),
        })
    # Сначала те, кто должен: это список, по которому звонят.
    out.sort(key=lambda row: (-row['debt'], row['client'].name))
    return out


def contracts():
    """Договоры: сколько ждёт подписи и сколько подписано."""
    rows = Contract.objects.exclude(status=Contract.Status.CANCELED)
    waiting = list(rows.filter(status=Contract.Status.ISSUED)
                   .select_related('project__client')[:10])
    return {
        'signed': rows.filter(status=Contract.Status.SIGNED).count(),
        'waiting': waiting,
        'waiting_count': rows.filter(status=Contract.Status.ISSUED).count(),
    }


def everything():
    """Всё сразу — один вызов для одной страницы."""
    refused, silent = refusals()
    return {
        'money': money(),
        'months': by_month(),
        'leads': leads(),
        'sources': sources(),
        'refusals': refused,
        'refusals_silent': silent,
        'clients': clients(),
        'contracts': contracts(),
    }
