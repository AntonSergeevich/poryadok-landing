"""Сборка разборов в текст для нейросети.

Пока поток клиентов небольшой, платить за обращения к нейросети незачем:
дешевле и честнее собрать ответы в один текст, скопировать и вставить в
чат руками. Здесь только сборка — никаких запросов наружу и никаких
ключей в проекте.

Когда разборов станет много и копировать надоест, сюда добавится функция,
которая отправит этот же текст в выбранный сервис. Формат текста менять
не придётся.

Личные данные наружу не идут: ни имени, ни телефона, ни ника. Остаются
только ответы — по ним человека не опознать.
"""
from .. import survey as sv

PROMPT = """Ты помогаешь владельцу небольшой студии, которая собирает
операционные системы для малого бизнеса. Ниже — обезличенные ответы
предпринимателей на разбор процессов.

Нужно:
1. Что повторяется у большинства — три-четыре главные боли с числами.
2. По каким сферам боли различаются.
3. Три темы для заметок в Telegram: про что писать, чтобы попасть в
   больное место. Заголовок и одна мысль на каждую.
4. Какие вопросы в разборе оказались бесполезными и что спросить вместо них.

Пиши спокойно и коротко, без слов «синергия», «масштабирование»,
«боль клиента». Так, как говорил бы инженер, а не продавец.

--- ОТВЕТЫ ---
"""


def one(entry, number=None):
    """Один разбор в виде текста. Без имени и контактов."""
    head = f'### Разбор {number}' if number else '### Разбор'
    lines = [head, f'Дата: {entry.created_at:%d.%m.%Y}']

    result = entry.diagnose()
    if result['top']:
        pains = ', '.join(f'{i["title"].lower()} ({i["points"]})' for i in result['top'])
        lines.append(f'Главные боли: {pains}')
    money = result['estimate']
    if money and money['total_money']:
        lines.append(
            f'Оценка потерь: около {money["total_money"]:,.0f} руб. в месяц, '
            f'рутина {money["hours_month"]} часов в месяц'.replace(',', ' '))

    lines.append('')
    for question, said in sv.readable(entry.answers):
        lines.append(f'{question}')
        lines.append(f'    {said}')
    return '\n'.join(lines)


def as_text(queryset, with_prompt=True):
    """Пачка разборов одним текстом, готовым к вставке в чат."""
    parts = []
    for number, entry in enumerate(queryset, 1):
        parts.append(one(entry, number))
    body = '\n\n'.join(parts) if parts else 'Пока ни одного разбора.'
    return (PROMPT + body) if with_prompt else body


def summary(queryset):
    """Сводка по всем разборам: что и как часто отвечают.

    Это то, что можно посчитать без нейросети, — и часто этого хватает.
    """
    total = 0
    counts = {}
    areas = {key: 0 for key in sv.AREAS}
    money_total = 0
    money_count = 0

    for entry in queryset:
        total += 1
        for q in sv.QUESTIONS:
            if q['type'] == 'text':
                continue
            value = entry.answers.get(q['id'])
            if not value:
                continue
            for v in (value if isinstance(value, list) else [value]):
                counts.setdefault(q['id'], {})
                label = sv._label(q['id'], v)
                counts[q['id']][label] = counts[q['id']].get(label, 0) + 1
        for area, points in sv.score(entry.answers).items():
            areas[area] += points
        est = sv.estimate(entry.answers)
        if est and est['total_money']:
            money_total += est['total_money']
            money_count += 1

    return {
        'total': total,
        'counts': counts,
        'areas': sorted(areas.items(), key=lambda kv: kv[1], reverse=True),
        'money_avg': round(money_total / money_count, -3) if money_count else 0,
    }
