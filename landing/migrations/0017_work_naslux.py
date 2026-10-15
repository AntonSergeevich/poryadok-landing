"""Четвёртая работа в портфолио — NASLUX, свой сервис.

Миграцией, хотя миграция «Твёрдого знака» обещала быть последней в своём
роде. Причина та же, что тогда: работу добавляют со стороны кода, без
доступа к кабинету на сервере, а снимки, загруженные через кабинет, живут
в media и в репозиторий не попадают. Положить снимки в статику и завести
работу миграцией — единственный способ, при котором она появляется на
сайте вместе с выкладкой, а не после ручной загрузки.

Работы, которые вы снимаете и заводите сами, по-прежнему удобнее делать
в кабинете: раздел «Портфолио».
"""
from django.db import migrations


SLUG = 'naslux'


def bring_in(apps, schema_editor):
    from landing.works import by_slug

    item = by_slug(SLUG)
    if item is None:
        return

    Work = apps.get_model('landing', 'Work')
    WorkFact = apps.get_model('landing', 'WorkFact')
    WorkShot = apps.get_model('landing', 'WorkShot')

    # Работу могли уже завести руками — перезаписывать чужую правку нельзя.
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
        order=40,
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

    dependencies = [('landing', '0016_lead_hand_sources')]
    operations = [migrations.RunPython(bring_in, take_out)]
