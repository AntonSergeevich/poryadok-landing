"""Выгрузка разборов процессов — текстом, готовым к вставке в чат.

    python manage.py survey_export                  все, с готовым заданием
    python manage.py survey_export --days 30        только за последний месяц
    python manage.py survey_export --stories        только те, кто разрешил примеры
    python manage.py survey_export --summary        сводка цифрами, без нейросети
    python manage.py survey_export --raw            без задания, только ответы
    python manage.py survey_export > razbor.txt     сохранить в файл

Личные данные наружу не выгружаются: ни имени, ни телефона, ни ника.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from landing.models import Survey
from landing.services import analysis


class Command(BaseCommand):
    help = 'Собирает разборы процессов в текст для анализа'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=0,
                            help='только за последние N дней')
        parser.add_argument('--stories', action='store_true',
                            help='только разрешившие использовать как пример')
        parser.add_argument('--summary', action='store_true',
                            help='сводка цифрами вместо выгрузки')
        parser.add_argument('--raw', action='store_true',
                            help='без задания для нейросети')

    def handle(self, *args, **options):
        qs = Survey.objects.all().order_by('created_at')
        if options['days']:
            qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=options['days']))
        if options['stories']:
            qs = qs.filter(allow_stories=True)

        if not qs.exists():
            self.stdout.write('Подходящих разборов нет.')
            return

        if options['summary']:
            self._summary(qs)
            return

        self.stdout.write(analysis.as_text(qs, with_prompt=not options['raw']))

    def _summary(self, qs):
        data = analysis.summary(qs)
        w = self.stdout.write

        w(f'\nРазборов: {data["total"]}')
        if data['money_avg']:
            w(f'Средняя оценка потерь: {data["money_avg"]:,.0f} ₽ в месяц'.replace(',', ' '))

        w('\nОбласти боли, суммарно по всем:')
        from landing.survey import AREAS
        for area, points in data['areas']:
            w(f'  {AREAS[area]:<38} {points}')

        w('\nЧто отвечают:')
        from landing.survey import QUESTIONS_BY_ID
        for qid, answers in data['counts'].items():
            q = QUESTIONS_BY_ID.get(qid)
            w(f'\n  {q["title"] if q else qid}')
            for label, count in sorted(answers.items(), key=lambda kv: -kv[1]):
                share = round(count / data['total'] * 100)
                w(f'    {count:>3} ({share:>3}%)  {label}')
        w('')
