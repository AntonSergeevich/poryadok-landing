"""Третья работа в портфолио — «Твёрдый знак».

Миграцией, а не через кабинет, по одной причине: снимки этой работы лежат
в статике, вместе с двумя первыми. Загруженные из кабинета попадают
в media, и на чистой машине их пришлось бы восстанавливать из копии —
а портфолио должно подниматься вместе с кодом.

Дальше так делать не нужно. Работы, снятые после этой, заводятся
в кабинете: раздел «Портфолио», кнопка из проекта. Эта миграция —
последняя в своём роде, и добавлять сюда четвёртую не надо.

Тексты и подписи берутся из landing/works.py — там же, где лежат первые
две.
"""
from django.db import migrations


SLUG = 'tznak'


def bring_in(apps, schema_editor):
    from landing.works import by_slug

    item = by_slug(SLUG)
    if item is None:
        return

    Work = apps.get_model('landing', 'Work')
    WorkFact = apps.get_model('landing', 'WorkFact')
    WorkShot = apps.get_model('landing', 'WorkShot')

    # Повторный запуск ничего не портит: работу могли уже поправить руками,
    # и перезаписывать её значит отменить чужую правку.
    if Work.objects.filter(slug=SLUG).exists():
        return

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
        order=30,
        # Работа показывается сразу: её и просили добавить в портфолио.
        # Адрес сайта пуст — неизвестно, выложена ли система под своим
        # именем. Строка «Сайт» из-за этого просто не рисуется, ссылки
        # в никуда не появляется; впишут адрес в кабинете — строка
        # появится сама.
        is_published=True,
    )
    for n, (label, value) in enumerate(item.get('facts', ()), start=1):
        WorkFact.objects.create(work=work, label=label, value=value,
                                order=n * 10)
    for n, shot in enumerate(item.get('shots', ()), start=1):
        WorkShot.objects.create(work=work, static_name=shot['file'],
                                caption=shot.get('cap', ''), order=n * 10)


def take_out(apps, schema_editor):
    Work = apps.get_model('landing', 'Work')
    Work.objects.filter(slug=SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [('landing', '0013_lead_build_blocks_lead_build_scale_and_more')]
    operations = [migrations.RunPython(bring_in, take_out)]
