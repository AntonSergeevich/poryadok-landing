"""Разбор процессов: вопросы, подсчёт и выводы.

Здесь лежит всё содержательное, чтобы менять формулировки не трогая код.
Вопросы описаны данными, форма и страница строятся из них сами.

Правила тона те же, что на сайте: короткие фразы, слова клиента, никакого
«масштабирования» и «синергии». Вопрос должен читаться так, будто его задал
живой человек, который сам держал бизнес.

Устройство одного вопроса:

    {
      'id':      'lost',                  ключ, под ним ответ ложится в базу
      'icon':    'leak',                  имя значка из набора в survey.html
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
        'icon': 'shop',
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
        'icon': 'people',
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
        'icon': 'funnel',
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
        'icon': 'drawers',
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
        'icon': 'leak',
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
        'icon': 'clock',
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
        'icon': 'calendar',
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
        'icon': 'noshow',
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
        'icon': 'money',
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
        'icon': 'loop',
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
        'icon': 'power',
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
        'icon': 'bars',
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
        'icon': 'receipt',
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
        # Развилка. Без неё тест считал всех потоком, а самый маленький
        # ответ про клиентов — «до двадцати» — брался как двенадцать
        # человек в месяц. Для архитектора с шестью проектами в год это
        # завышение в пятнадцать раз, и завышается вместе с ним весь итог.
        'id': 'rhythm',
        'icon': 'grid',
        'title': 'Клиенты приходят потоком или проектами?',
        'hint': 'От этого зависит, как считать. Ошибиться тут — ошибиться во всём',
        'type': 'one',
        'required': True,
        'options': [
            {'value': 'flow',    'label': 'Потоком: каждый месяц примерно одинаково'},
            {'value': 'project', 'label': 'Проектами: несколько крупных за год'},
        ],
    },
    {
        'id': 'clients',
        'icon': 'grid',
        'title': 'Сколько клиентов вы обслуживаете за месяц?',
        # Показывается только тем, у кого поток. Без JavaScript видны оба
        # вопроса — и это правильно: скрыть вопрос нечем, а лишний ответ
        # ничего не портит, потому что считается только подходящий.
        'show_if': ('rhythm', 'flow'),
        'type': 'one',
        'required': False,
        'options': [
            {'value': 'n12',  'label': 'До двадцати'},
            {'value': 'n35',  'label': 'От двадцати до пятидесяти'},
            {'value': 'n100', 'label': 'От пятидесяти до ста пятидесяти'},
            {'value': 'n300', 'label': 'От ста пятидесяти до пятисот'},
            {'value': 'n700', 'label': 'Больше пятисот'},
        ],
    },
    {
        'id': 'projects_year',
        'icon': 'grid',
        'title': 'Сколько проектов вы берёте за год?',
        'hint': 'Крупные работы, а не отдельные визиты',
        'show_if': ('rhythm', 'project'),
        'type': 'one',
        'required': False,
        'options': [
            {'value': 'p3',  'label': 'Два-четыре'},
            {'value': 'p6',  'label': 'Пять-восемь'},
            {'value': 'p12', 'label': 'Девять-пятнадцать'},
            {'value': 'p24', 'label': 'Больше пятнадцати'},
        ],
    },
    {
        'id': 'pain',
        'icon': 'speech',
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
# Проектный ритм: сколько крупных работ за год. В месяц переводится делением,
# и дробное число клиентов в месяц здесь нормально — это средняя загрузка,
# а не количество людей в приёмной.
PROJECTS_MID = {'p3': 3, 'p6': 6, 'p12': 12, 'p24': 24}

# «Не знаю» в списке нет намеренно: доля потерь у того, кто их не считает,
# неизвестна. Раньше здесь стояло .12 — то есть человеку, честно
# ответившему «не считаю», система приписывала двенадцать процентов
# как факт. Цифра, построенная на чужом незнании, — худший вид цифры.
LOST_RATE   = {'day': .20, 'week': .12, 'month': .05, 'never': 0}
NOSHOW_RATE = {'few': .03, 'one': .10, 'few3': .25, 'many': .40, 'na': 0}
HOURS_MID   = {'h3': 3, 'h7': 7, 'h15': 15, 'h25': 25}

HOUR_PRICE = 700  # во сколько условно оценивать час владельца

# Потолок правдоподобия.
#
# На крайних ответах — теряем каждый день и не приходит больше трети —
# множитель к выручке выходил 0,92. То есть бизнесу с выручкой миллион
# тест сообщал, что он теряет девятьсот двадцать тысяч в месяц. В такое
# не верят, и правильно делают: цифра, в которую не верят, обесценивает
# и все остальные на странице.
#
# Потери сверх этой доли бывают. Но объяснять их надо голосом и с цифрами
# в руках, а не строкой в тесте.
LOSS_CAP = 0.35

# Насколько широка вилка. Модель грубая, и выглядеть она должна грубой:
# точное число оспаривают, вилку — нет.
SPREAD = 0.4


def spread(x, k=SPREAD):
    """Вилка вокруг оценки, краями по тысяче.

    Оценка построена на серединах диапазонов и трёх коэффициентах. Печатать
    её точным числом — значит выдать допущение за факт: человек читает
    «≈ 150 000» как измерение, приходит на разбор и обнаруживает, что
    измерения не было.
    """
    if not x:
        return None
    low = int(round(x * (1 - k), -3))
    high = int(round(x * (1 + k), -3))
    if high <= low:
        # На маленьких суммах края сходятся после округления. Вилка,
        # у которой края равны, — это точное число, только притворяющееся
        # вилкой.
        high = low + 1000
    return {'low': low, 'high': high}


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


def clients_per_month(answers):
    """Сколько клиентов в месяц — по тому ритму, который человек назвал.

    Два разных бизнеса считаются по-разному, и это не тонкость. У потока
    клиенты приходят каждый месяц примерно одинаково. У проектного
    ритма — несколько крупных работ за год, и месячная загрузка выводится
    делением. Считать проектного по шкале потока значит завысить его
    выручку в разы, а вместе с ней и всё, что от неё считается.
    """
    if answers.get('rhythm') == 'project':
        per_year = PROJECTS_MID.get(answers.get('projects_year'))
        return per_year / 12 if per_year else None
    return CLIENTS_MID.get(answers.get('clients'))


def basis(answers):
    """Из чего сложилась оценка — человеческими словами.

    Показывается под цифрой. Человек сразу видит допущение и, если оно
    неверно, поправляет его сам — вместо того чтобы уйти с ощущением,
    что ему назвали цифру с потолка.
    """
    out = []
    check = answers.get('check')
    if check:
        out.append(f'чек {_label("check", check).lower()}')

    if answers.get('rhythm') == 'project':
        if answers.get('projects_year'):
            out.append(f'{_label("projects_year", answers["projects_year"]).lower()} '
                       f'проектов за год')
    elif answers.get('clients'):
        out.append(f'{_label("clients", answers["clients"]).lower()} клиентов в месяц')

    lost = answers.get('lost')
    if lost and lost != 'idk':
        out.append(f'заявки: {_label("lost", lost).lower()}')

    noshow = answers.get('noshow')
    if noshow and noshow not in ('na',):
        out.append(f'не приходят: {_label("noshow", noshow).lower()}')
    return out


def estimate(answers):
    """Грубая оценка потерь в деньгах и часах.

    Считаем от того, что человек уже зарабатывает. Если из десяти заявок
    одна теряется, то теряется не десятая часть выручки, а ещё одна
    девятая сверх имеющейся — поэтому делим на (1 - доля), а не умножаем.
    """
    check = CHECK_MID.get(answers.get('check'))
    clients = clients_per_month(answers)
    if not check or not clients:
        return None

    revenue = check * clients
    lost_answer = answers.get('lost')
    noshow_rate = NOSHOW_RATE.get(answers.get('noshow'), 0)
    hours = HOURS_MID.get(answers.get('routine'), 0)

    # «Честно — не знаю, не считаю» деньгами не считается вовсе. Это
    # не пропуск в расчёте, а находка: деньги утекают ровно там, где их
    # никто не считает, и сказать это сильнее, чем назвать выдуманную сумму.
    lost_unknown = lost_answer == 'idk'
    if lost_unknown:
        lost_money = None
    else:
        lost_rate = LOST_RATE.get(lost_answer, 0)
        lost_money = revenue * lost_rate / (1 - lost_rate) if lost_rate < 1 else 0

    noshow_money = revenue * noshow_rate / (1 - noshow_rate) if noshow_rate < 1 else 0

    raw_total = (lost_money or 0) + noshow_money
    ceiling = revenue * LOSS_CAP
    capped = raw_total > ceiling
    total = min(raw_total, ceiling)

    # Слагаемые ужимаются вместе с итогом.
    #
    # Иначе страница спорит сама с собой: в заголовке вилка вокруг
    # трёхсот тысяч, а под ней два числа, которые в сумме дают
    # четыреста. Читатель, который складывает — а он и есть тот, ради
    # кого писалась вся эта честность, — увидит расхождение и перестанет
    # верить обеим цифрам сразу.
    if capped and raw_total:
        squeeze = ceiling / raw_total
        if lost_money is not None:
            lost_money *= squeeze
        noshow_money *= squeeze

    hours_month = round(hours * 4.3)

    # Целыми: при проектном ритме клиентов в месяц выходит дробное число,
    # и без этого на странице появилось бы «40 000.0 ₽».
    return {
        'revenue': int(round(revenue, -3)),
        'lost_money': None if lost_money is None else int(round(lost_money, -3)),
        'lost_unknown': lost_unknown,
        'noshow_money': int(round(noshow_money, -3)),
        'total_money': int(round(total, -3)),
        'total_range': spread(total),
        # Потолок сработал — значит на странице надо сказать об этом,
        # а не срезать молча. Молча срезанная цифра — та же выдумка,
        # только аккуратнее выглядящая.
        'capped': capped,
        'basis': basis(answers),
        'hours_month': hours_month,
        'hours_money': int(round(hours_month * HOUR_PRICE, -3)),
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
