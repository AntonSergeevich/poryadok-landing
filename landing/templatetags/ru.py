"""Русские окончания в шаблонах.

Встроенный `pluralize` рассчитан на две формы: «файл» и «файлы». Русскому
нужно три — 1 отказ, 2 отказа, 5 отказов, — и попытка передать третью
не срабатывает молча: Django возвращает пустое окончание, и на экране
остаётся «8 вопрос». Ошибка тихая, поэтому и живёт долго.
"""
from django import template

from ..contract import plural

register = template.Library()


@register.filter(name='ru_plural')
def ru_plural(number, forms):
    """{{ n }} {{ n|ru_plural:"вопрос,вопроса,вопросов" }}"""
    parts = [part.strip() for part in (forms or '').split(',')]
    if len(parts) != 3:
        return forms
    try:
        return plural(number, parts)
    except (TypeError, ValueError):
        return parts[2]
