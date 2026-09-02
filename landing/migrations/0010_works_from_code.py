"""Переносит две работы из landing/works.py в базу.

Данные те же самые — просто теперь они живут там, где их можно править
из кабинета. Файл landing/works.py после этого остаётся: он и есть
источник этой миграции, и удалять его значит потерять возможность
повторить перенос на чистой базе.

Снимки не копируются. Они лежат в статике и там же остаются: у снимка
есть поле `static_name` ровно для этого случая. Миграция, которая трогает
файлы, ломается там, где её труднее всего чинить, — на боевом сервере,
где у папки другой владелец.
"""
from django.db import migrations


# Слуги перечислены здесь списком, а не берутся из WORKS целиком.
#
# Миграция читает landing/works.py, а файл живёт и растёт: в него добавили
# третью работу — и эта миграция на чистой базе завела бы уже три, причём
# опубликованными, тогда как на сервере, где она давно применена, третью
# заводит миграция 0014 и заводит скрытой. Один и тот же код давал бы два
# разных результата в зависимости от того, когда его запустили.
#
# Миграция — это запись о том, что произошло тогда. Тогда работ было две.
SLUGS = ('dades', 'linguich')


def bring_in(apps, schema_editor):
    from landing.works import by_slug

    WORKS = [w for w in (by_slug(slug) for slug in SLUGS) if w]

    Work = apps.get_model('landing', 'Work')
    WorkFact = apps.get_model('landing', 'WorkFact')
    WorkShot = apps.get_model('landing', 'WorkShot')

    for order, item in enumerate(WORKS, start=1):
        # Повторный запуск ничего не портит: работу могли уже поправить
        # руками, и перезаписывать её значит отменить чужую правку.
        if Work.objects.filter(slug=item['slug']).exists():
            continue

        work = Work.objects.create(
            slug=item['slug'],
            title=item['title'],
            role=item['role'],
            site=item.get('site', ''),
            city=item.get('city', ''),
            term=item.get('term', ''),
            term_note=item.get('term_note', ''),
            lede=item.get('lede', ''),
            was_text='\n'.join(item.get('was', ())),
            now_text='\n'.join(item.get('now', ())),
            order=order * 10,
            is_published=True,
        )
        for n, (label, value) in enumerate(item.get('facts', ()), start=1):
            WorkFact.objects.create(work=work, label=label, value=value,
                                    order=n * 10)
        for n, shot in enumerate(item.get('shots', ()), start=1):
            WorkShot.objects.create(work=work, static_name=shot['file'],
                                    caption=shot.get('cap', ''), order=n * 10)


def take_out(apps, schema_editor):
    """Откат убирает только то, что принесла миграция.

    Работы, заведённые из кабинета после переноса, не трогаются: откатить
    структуру и заодно стереть чужую работу — это два разных действия,
    и второго здесь никто не просил.
    """
    Work = apps.get_model('landing', 'Work')
    Work.objects.filter(slug__in=SLUGS).delete()


class Migration(migrations.Migration):

    dependencies = [('landing', '0009_work_workfact_workshot')]
    operations = [migrations.RunPython(bring_in, take_out)]
