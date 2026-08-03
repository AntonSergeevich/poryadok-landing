"""Разбор процессов: вопросы, подсчёт и выводы.

Здесь лежит всё содержательное, чтобы менять формулировки не трогая код.
Вопросы описаны данными, форма и страница строятся из них сами.

Правила тона те же, что на сайте: короткие фразы, слова клиента, никакого
«масштабирования» и «синергии». Вопрос должен читаться так, будто его задал
живой человек, который сам держал бизнес.

Устройство одного вопроса:

    {
      'id':      'lost',                  ключ, под ним ответ ложится в базу
      'title':   'Как часто теряется…',   сам вопрос
      'hint':    'подсказка под ним',     необязательно
      'type':    'one' | 'many' | 'text',
      'required': True,
      'other':   True,                    добавить «Другое» со своим полем
      'options': [
          {'value': 'day', 'label': 'Каждый день', 'w': {'leads': 3}},
      ],
    }

`w` — насколько ответ добавляет боли в такую-то область. Ноль или отсутствие
означает «здесь всё в порядке».
"""

# ── Области боли ─────────────────────────────────────────────────────
# Названы результатом, а не модулем: клиент думает «заявки теряются»,
# а не «отсутствует CRM-система».

AREAS = {
    'leads':    'Заявки теряются',
    'schedule': 'Запись и расписание',
    'money':    'Непонятно, сколько заработали',
    'repeat':   'Клиенты не возвращаются',
    'owner':    'Всё держится на вас',
    'routine':  'Время уходит на рутину',
}

# Что говорить, когда область набрала много и когда немного.
VERDICTS = {
    'leads': {
        'high': 'Заявки теряются регулярно. Человек написал или позвонил — '
                'и остался без ответа. Это самые дорогие потери: за этих людей '
                'уже заплачено рекламой или репутацией.',
        'mid':  'Заявки теряются не каждый день, но теряются. Обычно это те, '
                'что пришли вечером, в выходной или сразу в несколько каналов.',
    },
    'schedule': {
        'high': 'Расписание живёт в голове и в переписках. Отсюда накладки, '
                'забытые записи и клиенты, которые не пришли и не предупредили.',
        'mid':  'Запись в целом держится, но напоминания и переносы отнимают '
                'внимание и иногда срываются.',
    },
    'money': {
        'high': 'Реальные цифры месяца видны только по остатку денег. Значит '
                'решения принимаются на ощущение, а не на факт.',
        'mid':  'Цифры собираются вручную. Это работает, пока хватает терпения '
                'считать, и ломается в первый занятой месяц.',
    },
    'repeat': {
        'high': 'С уже пришедшими клиентами никто не работает. Между тем '
                'вернуть человека, который у вас был, дешевле, чем привести нового.',
        'mid':  'Клиентов возвращаете вручную и по памяти. Часть просто '
                'забывается.',
    },
    'owner': {
        'high': 'Без вас останавливается. Это не про отпуск — это про то, что '
                'бизнес нельзя ни передать, ни продать, ни просто поболеть.',
        'mid':  'Без вас работает, но с потерями. Обычно проваливаются именно '
                'те места, где нет записанного порядка.',
    },
    'routine': {
        'high': 'На переписки, напоминания и отчёты уходит столько времени, '
                'что на саму работу и развитие его почти не остаётся.',
        'mid':  'Рутина занимает заметную часть недели. Её видно не сразу — '
                'она размазана по дню кусками по десять минут.',
    },
}

# ── Вопросы ──────────────────────────────────────────────────────────

QUESTIONS = [
    {
        'id': 'area',
        'title': 'Чем вы занимаетесь?',
        'type': 'one',
        'required': True,
        'other': True,
        'options': [
            {'value': 'beauty',   'label': 'Барбершоп, салон, студия красоты'},
            {'value': 'school',   'label': 'Школа, курсы, детский центр'},
            {'value': 'medical',  'label': 'Стоматология, медцентр, клиника'},
            {'value': 'build',    'label': 'Строительство, ремонт, монтаж'},
            {'value': 'expert',   'label': 'Частный специалист, практика'},
            {'value': 'shop',     'label': 'Магазин, торговля'},
            {'value': 'service',  'label': 'Услуги на выезде, сервис'},
        ],
    },
    {
        'id': 'team',
        'title': 'Сколько человек работает, включая вас?',
        'type': 'one',
        'required': True,
        'options': [
            {'value': 'solo', 'label': 'Я один'},
            {'value': '2_5',  'label': 'От двух до пяти'},
            {'value': '6_15', 'label': 'От шести до пятнадцати'},
            {'value': '15p',  'label': 'Больше пятнадцати'},
        ],
    },
    {
        'id': 'sources',
        'title': 'Откуда к вам приходят люди?',
        'hint': 'Можно отметить несколько',
        'type': 'many',
        'required': True,
        'other': True,
        'options': [
            {'value': 'word',    'label': 'Сарафан, по рекомендации'},
            {'value': 'maps',    'label': 'Карты — 2ГИС, Яндекс'},
            {'value': 'social',  'label': 'Соцсети и мессенджеры'},
            {'value': 'avito',   'label': 'Авито и доски объявлений'},
            {'value': 'site',    'label': 'Сайт'},
            {'value': 'calls',   'label': 'Звонки'},
            {'value': 'ads',     'label': 'Платная реклама'},
        ],
    },
    {
        'id': 'storage',
        'title': 'Где сейчас лежат заявки и контакты клиентов?',
        'type': 'one',
        'required': True,
        'other': True,
        'options': [
            {'value': 'head',  'label': 'В переписках и в голове',
             'w': {'leads': 3, 'repeat': 3, 'owner': 2}},
            {'value': 'paper', 'label': 'В тетради или на бумаге',
             'w': {'leads': 2, 'repeat': 3, 'owner': 2}},
            {'value': 'excel', 'label': 'В таблице — Excel или Google',
             'w': {'leads': 1, 'repeat': 2, 'money': 1}},
            {'value': 'crm',   'label': 'В программе для учёта клиентов',
             'w': {}},
            {'value': 'mixed', 'label': 'Везде понемногу',
             'w': {'leads': 3, 'repeat': 2, 'money': 2, 'owner': 2}},
        ],
    },
    {
        'id': 'lost',
        'title': 'Как часто заявка теряется?',
        'hint': 'Не перезвонили, забыли, ответили через день',
        'type': 'one',
        'required': True,
        'options': [
            {'value': 'day',   'label': 'Каждый день что-то теряется',
             'w': {'leads': 4, 'money': 1}},
            {'value': 'week',  'label': 'Несколько раз в неделю',
             'w': {'leads': 3}},
            {'value': 'month', 'label': 'Пару раз в месяц',
             'w': {'leads': 1}},
            {'value': 'never', 'label': 'Не теряются', 'w': {}},
            {'value': 'idk',   'label': 'Честно — не знаю, не считаю',
             'w': {'leads': 3, 'money': 2}},
        ],
    },
    {
        'id': 'reply',
        'title': 'За сколько обычно отвечаете на новую заявку?',
        'type': 'one',
        'required': True,
        'options': [
            {'value': 'min15', 'label': 'В течение пятнадцати минут', 'w': {}},
            {'value': 'hours', 'label': 'В течение нескольких часов',
             'w': {'leads': 1}},
            {'value': 'day',   'label': 'В течение дня',
             'w': {'leads': 2}},
            {'value': 'later', 'label': 'Когда получится — бывает и назавтра',
             'w': {'leads': 3, 'routine': 1}},
        ],
    },
    {
        'id': 'booking',
        'title': 'Кто ведёт запись и расписание?',
        'type': 'one',
        'required': True,
        'other': True,
        'options': [
            {'value': 'me',       'label': 'Я сам, в переписке',
             'w': {'schedule': 3, 'owner': 3, 'routine': 3}},
            {'value': 'admin',    'label': 'Администратор',
             'w': {'schedule': 1, 'routine': 1}},
            {'value': 'online',   'label': 'Клиент записывается сам, онлайн',
             'w': {}},
            {'value': 'none',     'label': 'Записи как таковой нет',
             'w': {'schedule': 2}},
        ],
    },
    {
        'id': 'noshow',
        'title': 'Сколько клиентов не приходит на запись?',
        'type': 'one',
        'required': True,
        'options': [
            {'value': 'few',  'label': 'Почти все приходят', 'w': {}},
            {'value': 'one',  'label': 'Примерно один из десяти',
             'w': {'schedule': 1}},
            {'value': 'few3', 'label': 'Двое-трое из десяти',
             'w': {'schedule': 3, 'money': 1}},
            {'value': 'many', 'label': 'Больше трёх из десяти',
             'w': {'schedule': 4, 'money': 2}},
            {'value': 'na',   'label': 'У меня нет записи', 'w': {}},
        ],
    },
    {
        'id': 'money_view',
        'title': 'Как вы понимаете, сколько заработали за месяц?',
        'type': 'one',
        'required': True,
        'other': True,
        'options': [
            {'value': 'rest',   'label': 'Смотрю, сколько осталось денег',
             'w': {'money': 4}},
            {'value': 'manual', 'label': 'Считаю вручную в таблице',
             'w': {'money': 2, 'routine': 2}},
            {'value': 'report', 'label': 'Открываю отчёт в программе', 'w': {}},
            {'value': 'idk',    'label': 'Честно — точно не знаю',
             'w': {'money': 4, 'owner': 1}},
        ],
    },
    {
        'id': 'repeat',
        'title': 'Клиенты возвращаются сами или про них надо вспоминать?',
        'type': 'one',
        'required': True,
        'options': [
            {'value': 'self',   'label': 'Возвращаются сами', 'w': {}},
            {'value': 'manual', 'label': 'Напоминаю вручную, когда дойдут руки',
             'w': {'repeat': 2, 'routine': 2}},
            {'value': 'none',   'label': 'Никак с этим не работаем',
             'w': {'repeat': 4}},
            {'value': 'auto',   'label': 'Напоминания уходят автоматически',
             'w': {}},
        ],
    },
    {
        'id': 'vacation',
        'title': 'Что будет, если вы уедете на неделю без связи?',
        'type': 'one',
        'required': True,
        'options': [
            {'value': 'stop',  'label': 'Всё встанет',
             'w': {'owner': 4, 'leads': 2}},
            {'value': 'loss',  'label': 'Будет работать, но с потерями',
             'w': {'owner': 2}},
            {'value': 'ok',    'label': 'Будет работать нормально', 'w': {}},
            {'value': 'never', 'label': 'Я не пробовал и боюсь проверять',
             'w': {'owner': 4}},
        ],
    },
    {
        'id': 'routine',
        'title': 'Сколько часов в неделю уходит на переписки, напоминания и отчёты?',
        'hint': 'Всё, что не сама работа и не общение с клиентом по делу',
        'type': 'one',
        'required': True,
        'options': [
            {'value': 'h3',  'label': 'До пяти часов', 'w': {}},
            {'value': 'h7',  'label': 'От пяти до десяти', 'w': {'routine': 2}},
            {'value': 'h15', 'label': 'От десяти до двадцати', 'w': {'routine': 3}},
            {'value': 'h25', 'label': 'Больше двадцати',
             'w': {'routine': 4, 'owner': 2}},
        ],
    },
    {
        'id': 'check',
        'title': 'Средний чек — сколько платит один клиент за раз?',
        'hint': 'Нужно, чтобы посчитать потери в деньгах. Достаточно примерно',
        'type': 'one',
        'required': True,
        'options': [
            {'value': 'c700',   'label': 'До тысячи рублей'},
            {'value': 'c2000',  'label': 'От тысячи до трёх тысяч'},
            {'value': 'c6000',  'label': 'От трёх до десяти тысяч'},
            {'value': 'c25000', 'label': 'От десяти до пятидесяти тысяч'},
            {'value': 'c80000', 'label': 'Больше пятидесяти тысяч'},
        ],
    },
    {
        'id': 'clients',
        'title': 'Сколько клиентов вы обслуживаете за месяц?',
        'type': 'one',
        'required': True,
        'options': [
            {'value': 'n12',  'label': 'До двадцати'},
            {'value': 'n35',  'label': 'От двадцати до пятидесяти'},
            {'value': 'n100', 'label': 'От пятидесяти до ста пятидесяти'},
            {'value': 'n300', 'label': 'От ста пятидесяти до пятисот'},
            {'value': 'n700', 'label': 'Больше пятисот'},
        ],
    },
    {
        'id': 'pain',
        'title': 'Что в работе бесит больше всего?',
        'hint': 'Своими словами. Это самый полезный ответ во всём разборе',
        'type': 'text',
        'required': False,
    },
]

QUESTIONS_BY_ID = {q['id']: q for q in QUESTIONS}

# ── Числа для оценки потерь ──────────────────────────────────────────
# Середины диапазонов. Оценка грубая и так и называется на странице.

CHECK_MID   = {'c700': 700, 'c2000': 2000, 'c6000': 6000,
               'c25000': 25000, 'c80000': 80000}
CLIENTS_MID = {'n12': 12, 'n35': 35, 'n100': 100, 'n300': 300, 'n700': 700}
LOST_RATE   = {'day': .20, 'week': .12, 'month': .05, 'never': 0, 'idk': .12}
NOSHOW_RATE = {'few': .03, 'one': .10, 'few3': .25, 'many': .40, 'na': 0}
HOURS_MID   = {'h3': 3, 'h7': 7, 'h15': 15, 'h25': 25}

HOUR_PRICE = 700  # во сколько условно оценивать час владельца


def _label(qid, value):
    """Человеческая подпись ответа вместо служебного кода."""
    q = QUESTIONS_BY_ID.get(qid)
    if not q:
        return value
    for opt in q.get('options', []):
        if opt['value'] == value:
            return opt['label']
    return value


def readable(answers):
    """Ответы в виде «Вопрос — ответ», для письма, админки и разбора."""
    out = []
    for q in QUESTIONS:
        value = answers.get(q['id'])
        if not value:
            continue
        if q['type'] == 'many':
            said = ', '.join(_label(q['id'], v) for v in value)
        elif q['type'] == 'text':
            said = value
        else:
            said = _label(q['id'], value)
        other = answers.get(q['id'] + '_other')
        if other:
            said = f'{said} ({other})' if said else other
        out.append((q['title'], said))
    return out


def score(answers):
    """Считает боль по областям. Возвращает {код области: очки}."""
    totals = {key: 0 for key in AREAS}
    for q in QUESTIONS:
        value = answers.get(q['id'])
        if not value:
            continue
        values = value if isinstance(value, list) else [value]
        for opt in q.get('options', []):
            if opt['value'] in values:
                for area, weight in opt.get('w', {}).items():
                    totals[area] += weight
    return totals


def estimate(answers):
    """Грубая оценка потерь в деньгах и часах.

    Считаем от того, что человек уже зарабатывает. Если из десяти заявок
    одна теряется, то теряется не десятая часть выручки, а ещё одна
    девятая сверх имеющейся — поэтому делим на (1 - доля), а не умножаем.
    """
    check = CHECK_MID.get(answers.get('check'))
    clients = CLIENTS_MID.get(answers.get('clients'))
    if not check or not clients:
        return None

    revenue = check * clients
    lost_rate = LOST_RATE.get(answers.get('lost'), 0)
    noshow_rate = NOSHOW_RATE.get(answers.get('noshow'), 0)
    hours = HOURS_MID.get(answers.get('routine'), 0)

    lost_money = revenue * lost_rate / (1 - lost_rate) if lost_rate < 1 else 0
    noshow_money = revenue * noshow_rate / (1 - noshow_rate) if noshow_rate < 1 else 0
    hours_month = round(hours * 4.3)

    return {
        'revenue': round(revenue, -3),
        'lost_money': round(lost_money, -3),
        'noshow_money': round(noshow_money, -3),
        'total_money': round(lost_money + noshow_money, -3),
        'hours_month': hours_month,
        'hours_money': round(hours_month * HOUR_PRICE, -3),
    }


def diagnose(answers):
    """Готовый разбор: главные боли, оценка потерь, что делать первым."""
    totals = score(answers)
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    top = []
    for area, points in ranked:
        if points <= 0:
            continue
        level = 'high' if points >= 5 else 'mid'
        top.append({
            'key': area,
            'title': AREAS[area],
            'points': points,
            'level': level,
            'text': VERDICTS[area][level],
        })
        if len(top) == 3:
            break

    healthy = [AREAS[a] for a, p in ranked if p == 0]

    return {
        'top': top,
        'healthy': healthy,
        'totals': totals,
        'estimate': estimate(answers),
        'first_step': top[0]['title'] if top else None,
    }
