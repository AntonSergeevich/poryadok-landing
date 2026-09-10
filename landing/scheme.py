"""Схема процессов — то, что человек уносит с разбора.

Сайт обещает: «схему своих процессов вы заберёте себе, даже если мы
не станем работать дальше». До сих пор обещание выполнялось только на
живой встрече. Здесь оно выполняется сразу после теста, из его же
ответов, — и это единственная страница, которую человек может унести,
ничего не оставив взамен.

Схема рисуется на сервере обычным SVG: её печатают и сохраняют в PDF,
а печать не должна зависеть от того, выполнился ли скрипт.

Устройство одного шага:

    {
      'id':     'lead',              ключ шага
      'title':  'Заявка',            как называется на чертеже
      'leak_if': (('storage', {'head', 'paper'}), ...)
                                     когда шаг считается протекающим:
                                     любое совпадение из списка
      'says':   ('storage', 'lost'), чьи ответы подписывают шаг
      'ok':     'что здесь в порядке',
      'leak':   'что здесь течёт',
      'fix':    'booking',           блок конструктора, который это закрывает
    }

Условия течи описаны данными и повторяют варианты из landing/survey.py.
Разойтись они не должны: проверка в тестах требует, чтобы каждое
названное здесь значение существовало там.
"""
from . import constructor as build
from .survey import _label

# ── Шаги ─────────────────────────────────────────────────────────────
# Пять шагов пути клиента. Не «модули системы»: человек не покупает
# модули, он теряет людей между этими пятью точками.

STEPS = (
    {
        'id': 'lead',
        'title': 'Заявка',
        'says': ('storage', 'lost'),
        'leak_if': (('storage', {'head', 'paper', 'mixed'}),
                    ('lost', {'day', 'week'}),
                    ('reply', {'day', 'later'})),
        'ok': 'Обращение попадает в одно место, и у него есть следующий шаг.',
        'leak': 'Обращения расходятся по перепискам. Часть не доходит '
                'до ответа, и узнать об этом неоткуда.',
        'fix': 'leads',
    },
    {
        'id': 'booking',
        'title': 'Запись',
        'says': ('booking',),
        'leak_if': (('booking', {'me', 'none'}),),
        'ok': 'Время видно, занятое закрывается, освободившееся открывается.',
        'leak': 'Расписание держится на человеке. Накладки и забытые '
                'записи — вопрос времени, а не аккуратности.',
        'fix': 'booking',
    },
    {
        'id': 'remind',
        'title': 'Напоминание',
        'says': ('noshow',),
        'leak_if': (('noshow', {'one', 'few3', 'many'}),),
        'ok': 'Клиенту приходит напоминание, и до визита доходят почти все.',
        'leak': 'Напоминание либо не уходит, либо уходит руками. Каждый '
                'неприход — оплаченное время, которое уже не продать.',
        'fix': 'reminders',
    },
    {
        'id': 'money',
        'title': 'Оплата',
        'says': ('money_view',),
        'leak_if': (('money_view', {'rest', 'idk', 'manual'}),),
        'ok': 'Видно, кто заплатил и сколько, без сверки переводов руками.',
        'leak': 'Итог месяца собирается вручную или по остатку денег. '
                'Решения принимаются на ощущение, а не на факт.',
        'fix': 'money',
    },
    {
        'id': 'repeat',
        'title': 'Возврат',
        'says': ('repeat',),
        'leak_if': (('repeat', {'manual', 'none'}),),
        'ok': 'Клиента зовут обратно вовремя, и это происходит само.',
        'leak': 'Про клиента вспоминают, когда вспомнят. Дороже всего '
                'здесь те, кто уже приходил и остался доволен.',
        'fix': 'cabinet',
    },
)

# Полоса под схемой: всё это держится на владельце.
OWNER_LEAK = (('vacation', {'stop', 'loss'}), ('routine', {'h15', 'h25'}))

# ── Размеры чертежа ──────────────────────────────────────────────────
# Считаются здесь, а не в шаблоне: в шаблоне логике не место, а без
# арифметики он не расставит и двух прямоугольников.
#
# Текст в SVG сам не переносится: длинная строка не сжимается и не
# уходит на вторую, а просто вылезает за рамку и ложится поверх
# соседней подписи. Поэтому переносим здесь, посчитав, сколько
# знаков помещается в моноширинном кегле.

WIDTH = 660
PAD_TOP = 54
BOX_H = 104
GAP = 20
BOX_X = 20
BOX_W = 572
TEXT_DX = 52
LINE_H = 15
CHARS_PER_LINE = 72
MAX_LINES = 3


def _hit(answers, rules):
    """Сработало ли хоть одно условие из списка."""
    for question_id, values in rules:
        if answers.get(question_id) in values:
            return True
    return False


def _wrap(text, y):
    """Строки подписи с готовыми координатами.

    Ширина считается в знаках: шрифт моноширинный, и это единственный
    случай, когда посчитать перенос без разметки вообще возможно.
    """
    words = text.split()
    lines, current = [], ''
    for word in words:
        candidate = f'{current} {word}'.strip()
        if len(candidate) > CHARS_PER_LINE and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    lines = lines[:MAX_LINES]
    return [{'text': line, 'y': y + n * LINE_H} for n, line in enumerate(lines)]


def _said(answers, question_ids):
    """Что человек ответил — его словами, а не кодами."""
    words = []
    for question_id in question_ids:
        value = answers.get(question_id)
        if not value:
            continue
        words.append(_label(question_id, value))
    return '; '.join(words)


def build_scheme(answers):
    """Схема процессов по ответам разбора.

    Возвращает готовый к отрисовке чертёж: шаги с координатами,
    подписи и итог. Ничего не считает второй раз — течёт шаг или нет,
    решают те же ответы, что и оценка потерь.
    """
    steps = []
    y = PAD_TOP
    for number, described in enumerate(STEPS, start=1):
        leaking = _hit(answers, described['leak_if'])
        block = build.by_id(described['fix'])
        verdict = described['leak'] if leaking else described['ok']
        steps.append({
            'id': described['id'],
            'n': f'{number:02d}',
            'title': described['title'],
            'leaking': leaking,
            'said': _said(answers, described['says']),
            'verdict': verdict,
            'lines': _wrap(verdict, y + 70),
            'fix': block['title'] if leaking else '',
            'y': y,
            'title_y': y + 30,
            'said_y': y + 50,
            'mark_y': y + 30,
            'fix_y': y + BOX_H - 12,
            # Стрелка к следующему шагу. У последнего её нет: дальше
            # цикл, а он рисуется отдельной линией сбоку.
            'arrow_y': y + BOX_H,
            'arrow_tip': y + BOX_H + GAP - 6,
            'last': number == len(STEPS),
        })
        y += BOX_H + GAP

    leaks = [step for step in steps if step['leaking']]
    owner = _hit(answers, OWNER_LEAK)

    return {
        'steps': steps,
        'leaks': leaks,
        'leak_count': len(leaks),
        'owner_leak': owner,
        'width': WIDTH,
        'height': y - GAP + 20,
        'box_x': BOX_X,
        'box_w': BOX_W,
        'box_h': BOX_H,
        'text_x': BOX_X + TEXT_DX,
        # Подписи справа стоят внутри рамки, а не на её краю: на краю
        # они налезают на линию цикла.
        'right_x': BOX_X + BOX_W - 16,
        'mid_x': BOX_X + BOX_W // 2,
        'loop_x': BOX_X + BOX_W + 8,
        'loop_out': BOX_X + BOX_W + 26,
        'loop_top': PAD_TOP - 14,
        'loop_bottom': y - GAP - BOX_H // 2,
        'first_leak': leaks[0]['title'] if leaks else '',
    }
