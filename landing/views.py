"""Представления сайта.

Правило одно: заявка сначала попадает в базу, и только потом мы пытаемся
что-то отправить в Telegram. Если Telegram лежит — заявка всё равно наша.
"""
import json
import logging

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import ClubForm, LeadForm, SurveyForm
from .models import (Client, ClubSubscription, Lead, Payment, Survey,
                     normalize_phone)
from .services import getplatinum as gp
from .services import payments as pay
from .services import telegram as tg
from .survey import QUESTIONS

logger = logging.getLogger(__name__)

CLUB_PRICES = {'month': 3900, 'quarter': 9900, 'year': 34900}


def _save_lead(form, source, comment=''):
    """Сохраняет заявку и пробует уведомить в Telegram. Сохранение важнее."""
    lead = form.save(commit=False)
    lead.source = source
    if comment:
        lead.comment = comment
    lead.save()
    try:
        lead.delivered_to_telegram = tg.notify_lead(lead)
    except Exception:  # уведомление никогда не должно ронять заявку
        logger.exception('Не удалось уведомить о заявке %s', lead.pk)
        lead.delivered_to_telegram = False
    lead.save(update_fields=['delivered_to_telegram', 'updated_at'])
    if not lead.delivered_to_telegram:
        logger.error('Заявка %s сохранена, но не ушла в Telegram', lead.pk)
    return lead


def index(request):
    form = LeadForm(request.POST or None)
    success = False

    if request.method == 'POST':
        if form.is_valid():
            _save_lead(form, Lead.Source.FORM)
            request.session['lead_sent'] = True
            return redirect(reverse('index') + '?ok=1#cta')

    if request.GET.get('ok') and request.session.pop('lead_sent', False):
        success = True
        form = LeadForm()

    return render(request, 'landing/index.html', {
        'form': form,
        'success': success,
        'club_prices': CLUB_PRICES,
    })


def club(request):
    """Закрытый клуб: заявка или оплата, если подключён эквайринг."""
    form = ClubForm(request.POST or None)
    success = False

    if request.method == 'POST' and form.is_valid():
        plan = form.cleaned_data.get('plan') or 'month'
        lead = _save_lead(form, Lead.Source.CLUB,
                          comment=f'Тариф: {CLUB_PRICES.get(plan, 0)} ₽ ({plan})')

        url = _start_club_payment(request, lead, plan)
        if url:
            return redirect(url)

        request.session['club_sent'] = True
        return redirect(reverse('club') + '?ok=1#join')

    if request.GET.get('ok') and request.session.pop('club_sent', False):
        success = True
        form = ClubForm()

    return render(request, 'landing/club.html', {
        'form': form,
        'success': success,
        'prices': {k: f'{v:,}'.replace(',', ' ') for k, v in CLUB_PRICES.items()},
        'payments_enabled': gp.is_enabled() or pay.is_enabled(),
    })


def _start_club_payment(request, lead, plan):
    """Заводит подписку и платёж, возвращает адрес формы оплаты.

    Поставщиков два, и выбор простой: если настроен GetPlatinum — платим
    через него, иначе через ЮKassa, иначе оплаты нет вовсе и человек
    идёт обычным путём заявки. Возврат None означает именно это: не
    ошибку, а «оплату сейчас не провести, ведите как заявку».
    """
    provider = 'getplatinum' if gp.is_enabled() else ('yookassa' if pay.is_enabled() else None)
    if not provider:
        return None

    client = _client_from_lead(lead)
    subscription = ClubSubscription.objects.create(
        client=client, plan=plan, price=CLUB_PRICES.get(plan, 0))
    payment = Payment.objects.create(
        client=client, amount=subscription.price,
        purpose=Payment.Purpose.CLUB, provider=provider,
        payer_phone=lead.phone, payer_telegram=lead.telegram_username)
    subscription.payment = payment
    subscription.save(update_fields=['payment', 'updated_at'])

    title = f'Клуб «Порядок», {subscription.get_plan_display().lower()}'
    done_url = request.build_absolute_uri(reverse('club_done'))

    if provider == 'getplatinum':
        # Идентификатор заказа придумываем сами и до обращения к API:
        # по нему потом найдём платёж, когда придёт уведомление.
        deal_id = f'CLUB-{payment.pk}'
        _, form_url = gp.create_payment(
            deal_id=deal_id,
            amount=subscription.price,
            title=title,
            client_id=f'CLIENT-{client.pk}',
            notification_url=request.build_absolute_uri(reverse('getplatinum_webhook')),
            success_url=done_url,
            fail_url=request.build_absolute_uri(reverse('club')) + '?pay=fail#join',
            phone=lead.phone,
            name=lead.name,
            custom={'payment_pk': str(payment.pk)},
        )
        if form_url:
            payment.provider_payment_id = deal_id
            payment.save(update_fields=['provider_payment_id', 'updated_at'])
            return form_url
    else:
        payment_id, confirmation_url = pay.create_payment(
            amount=subscription.price,
            description=title,
            return_url=done_url,
            metadata={'payment_pk': str(payment.pk)},
        )
        if payment_id and confirmation_url:
            payment.provider_payment_id = payment_id
            payment.save(update_fields=['provider_payment_id', 'updated_at'])
            return confirmation_url

    # Платёжная система не ответила — человека не теряем, ведём заявкой.
    logger.error('Не удалось создать платёж (%s), заявка %s остаётся ручной',
                 provider, lead.pk)
    return None


def club_telegram(request):
    """Вход через Telegram: узнаём числовой id человека.

    Ради этого id всё и затевается. Ник для Telegram — просто подпись,
    её можно сменить за секунду; исключить из канала API умеет только по
    числовому id. Без входа доступ приходится закрывать руками.

    Данные приходят строкой запроса и потому доверия не заслуживают.
    Подлинность проверяет tg.verify_login — всё, что она не подтвердила,
    считаем подделкой.
    """
    data = tg.verify_login(request.GET.dict())
    if not data:
        return render(request, 'landing/club_access.html',
                      {'state': 'bad'}, status=400)

    user_id = data['id']
    username = (data.get('username') or '').lstrip('@')
    name = ' '.join(x for x in (data.get('first_name'), data.get('last_name')) if x)

    client = Client.objects.filter(telegram_user_id=user_id).first()
    if not client and username:
        client = Client.objects.filter(telegram_username__iexact=username).first()

    if client:
        # Запоминаем id: в следующий раз узнаем человека даже после смены ника.
        fields = []
        if client.telegram_user_id != user_id:
            client.telegram_user_id = user_id
            fields.append('telegram_user_id')
        if username and client.telegram_username != username:
            client.telegram_username = username
            fields.append('telegram_username')
        if fields:
            client.save(update_fields=fields + ['updated_at'])

    subscription = None
    if client:
        subscription = client.club_subscriptions.filter(
            status=ClubSubscription.Status.ACTIVE,
            ends_at__gt=timezone.now()).order_by('-ends_at').first()

    if not subscription:
        logger.info('Вход через Telegram: %s (%s) без активной подписки', name, user_id)
        return render(request, 'landing/club_access.html', {
            'state': 'no_sub', 'name': name, 'username': username,
        })

    # Ссылка одноразовая: переслать её другому бессмысленно.
    if not subscription.invite_link:
        link = tg.create_club_invite(name or username or str(user_id))
        if link:
            subscription.invite_link = link
            subscription.invite_sent_at = timezone.now()
            subscription.save(update_fields=['invite_link', 'invite_sent_at', 'updated_at'])
        else:
            logger.error('Не удалось создать приглашение для подписки %s', subscription.pk)

    return render(request, 'landing/club_access.html', {
        'state': 'ok' if subscription.invite_link else 'no_link',
        'name': name,
        'subscription': subscription,
    })


def club_done(request):
    """Куда возвращается человек после оплаты. Статус подтверждает вебхук."""
    return render(request, 'landing/club_done.html')


def _client_from_lead(lead):
    """Находит клиента по телефону или заводит нового."""
    client = Client.objects.filter(phone=normalize_phone(lead.phone)).first()
    if client is None:
        client = Client.objects.create(
            name=lead.name or 'Без имени', phone=lead.phone,
            area=lead.area, telegram_username=lead.telegram_username)
    elif lead.telegram_username and not client.telegram_username:
        client.telegram_username = lead.telegram_username
        client.save(update_fields=['telegram_username', 'updated_at'])
    lead.client = client
    lead.save(update_fields=['client', 'updated_at'])
    return client


@csrf_exempt
@require_POST
def yookassa_webhook(request):
    """Уведомление от ЮKassa.

    Телу запроса не верим: берём из него только идентификатор платежа
    и переспрашиваем статус у API. Иначе оплату мог бы подделать любой,
    кто знает адрес этого обработчика.
    """
    try:
        body = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest('bad json')

    payment_id = (body.get('object') or {}).get('id')
    if not payment_id:
        return HttpResponseBadRequest('no payment id')

    remote = pay.fetch_payment(payment_id)
    if not remote:
        # Не подтвердили — просим ЮKassa повторить попытку позже.
        return HttpResponse(status=503)

    payment = Payment.objects.filter(provider_payment_id=payment_id).first()
    if payment is None:
        logger.warning('Вебхук по неизвестному платежу %s', payment_id)
        return JsonResponse({'ok': True})

    status = remote.get('status')
    if status == 'succeeded' and remote.get('paid'):
        with transaction.atomic():
            newly_paid = payment.mark_succeeded()
        if newly_paid:
            _grant_club_access(payment)
    elif status == 'canceled':
        payment.status = Payment.Status.CANCELED
        payment.save(update_fields=['status', 'updated_at'])

    return JsonResponse({'ok': True})


@csrf_exempt
@require_POST
def getplatinum_webhook(request):
    """Уведомление об оплате от GetPlatinum.

    Две проверки подряд, и обе нужны:

    1. Подпись. Уведомление подписано нашим ключом API — без него
       подделать его нельзя. Это отсекает всех посторонних.
    2. Переспрос статуса. Даже подписанному уведомлению не верим
       на слово: спрашиваем у GetPlatinum, что с платежом на самом
       деле. Так советует и сама документация.

    Порядок именно такой, и это важно. Решает второй шаг, а не первый:
    пример подписи в документации GetPlatinum противоречив, и пока она
    не подтверждена настоящим уведомлением, несовпадение только пишется
    в журнал. Подделать же ответ /status нельзя — он приходит с их
    сервера по нашему ключу API. Когда подпись подтвердится, в .env
    ставится GETPLATINUM_STRICT_CHECKSUM=True, и первый шаг становится
    обязательным. Подробности — в landing/services/getplatinum.py.

    В ответ обязательно 200: при любом другом коде GetPlatinum
    повторную попытку не делает, и оплата останется неучтённой.
    Поэтому «не знаю такой платёж» — тоже 200, иначе мы просто
    потеряем уведомление навсегда.
    """
    try:
        body = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest('bad json')

    if not gp.verify(body) and gp.checksum_required():
        return HttpResponseBadRequest('bad checksum')

    deal_id = body.get('dealId')
    if not deal_id:
        return JsonResponse({'ok': True})

    payment = Payment.objects.filter(provider='getplatinum',
                                     provider_payment_id=str(deal_id)).first()
    if payment is None:
        logger.warning('GetPlatinum: уведомление по неизвестному заказу %s', deal_id)
        return JsonResponse({'ok': True})

    status = gp.fetch_status(deal_id)
    if status is None:
        # Статус не подтвердили — платёж не трогаем. Повтора не будет,
        # поэтому оставляем след в журнале: разберём вручную.
        logger.error('GetPlatinum: не удалось подтвердить статус заказа %s', deal_id)
        return JsonResponse({'ok': True})

    if gp.is_paid(status):
        with transaction.atomic():
            newly_paid = payment.mark_succeeded()
        if newly_paid:
            _grant_club_access(payment)
    else:
        payment.status = Payment.Status.CANCELED
        payment.save(update_fields=['status', 'updated_at'])
        logger.info('GetPlatinum: заказ %s не оплачен', deal_id)

    return JsonResponse({'ok': True})


def _grant_club_access(payment):
    """После успешной оплаты включает подписку и выдаёт ссылку в канал."""
    subscription = getattr(payment, 'club_subscription', None)
    if subscription is None:
        return
    subscription.activate()

    link = tg.create_club_invite(name_hint=subscription.client.name)
    if link:
        subscription.invite_link = link
        subscription.invite_sent_at = subscription.updated_at
        subscription.save(update_fields=['invite_link', 'invite_sent_at', 'updated_at'])

    tg.notify(
        'ПОРЯДОК // ОПЛАЧЕН КЛУБ\n'
        + '-' * 32 + '\n'
        + f'Клиент: {subscription.client.name}\n'
        + f'Телефон: {subscription.client.phone_pretty}\n'
        + f'Telegram: @{subscription.client.telegram_username or "—"}\n'
        + f'Тариф: {subscription.get_plan_display()} · {subscription.price:.0f} ₽\n'
        + f'Доступ до: {subscription.ends_at:%d.%m.%Y}\n'
        + '-' * 32 + '\n'
        + (f'Ссылка-приглашение: {link}' if link
           else 'Ссылку создать не удалось — выдайте доступ вручную.')
    )


def survey(request):
    """Разбор процессов: тест из landing/survey.py.

    Работает и без JS: тогда это одна длинная форма. Со скриптом вопросы
    показываются по одному. Контакт необязателен — человек имеет право
    просто посмотреть на себя со стороны, и это честнее, чем держать
    результат в заложниках.
    """
    form = SurveyForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        entry = Survey.objects.create(
            name=form.cleaned_data.get('name', '').strip(),
            phone=form.cleaned_data.get('phone', ''),
            telegram_username=form.cleaned_data.get('telegram_username', ''),
            answers=form.answers(),
            allow_stories=form.cleaned_data.get('allow_stories', False),
        )

        # Заявку заводим только если оставили телефон — иначе звонить некуда.
        if entry.phone:
            lead = Lead.objects.create(
                name=entry.name, phone=entry.phone,
                telegram_username=entry.telegram_username,
                area=entry.area, source=Lead.Source.SURVEY,
                comment='Прошёл разбор процессов на сайте.')
            entry.lead = lead
            entry.save(update_fields=['lead', 'updated_at'])

        try:
            entry.delivered_to_telegram = tg.notify_survey(entry)
        except Exception:
            logger.exception('Не удалось уведомить о разборе %s', entry.pk)
            entry.delivered_to_telegram = False
        entry.save(update_fields=['delivered_to_telegram', 'updated_at'])
        if entry.lead:
            entry.lead.delivered_to_telegram = entry.delivered_to_telegram
            entry.lead.save(update_fields=['delivered_to_telegram', 'updated_at'])

        request.session['survey_id'] = entry.pk
        return redirect('survey_done')

    return render(request, 'landing/survey.html', {
        'form': form,
        'steps': _survey_steps(form),
        'total': len(QUESTIONS),
    })


def _survey_steps(form):
    """Готовит вопросы к отрисовке.

    Разметку вариантов пишем руками, а не через {{ field }}, — значит
    отметку «выбрано» надо посчитать здесь. В шаблоне логике не место.
    """
    data = form.data if form.is_bound else {}
    steps = []
    for q in QUESTIONS:
        chosen = data.getlist(q['id']) if hasattr(data, 'getlist') else []
        options = [{
            'value': o['value'],
            'label': o['label'],
            'checked': o['value'] in chosen,
        } for o in q.get('options', [])]
        if q.get('other'):
            options.append({'value': 'other', 'label': 'Другое',
                            'checked': 'other' in chosen, 'is_other': True})
        steps.append({
            'q': q,
            'field': form[q['id']],
            'many': q['type'] == 'many',
            'is_text': q['type'] == 'text',
            'options': options,
            'other_name': q['id'] + '_other' if q.get('other') else '',
            'other_value': data.get(q['id'] + '_other', '') if data else '',
            'other_error': (form[q['id'] + '_other'].errors
                            if q.get('other') and form.is_bound else None),
        })
    return steps


def survey_done(request):
    """Результат разбора. Показывается один раз, по метке в сессии."""
    entry_id = request.session.pop('survey_id', None)
    if not entry_id:
        return redirect('survey')
    try:
        entry = Survey.objects.get(pk=entry_id)
    except Survey.DoesNotExist:
        return redirect('survey')

    return render(request, 'landing/survey_done.html', {
        'entry': entry,
        'result': entry.diagnose(),
        'left_contact': bool(entry.phone),
    })


def privacy(request):
    return render(request, 'landing/privacy.html', {
        'owner': settings.SITE_OWNER,
        'phone': settings.SITE_PHONE,
        'phone_pretty': settings.SITE_PHONE_PRETTY,
        'email': settings.SITE_EMAIL,
    })


def robots_txt(request):
    host = request.get_host()
    lines = [
        'User-agent: *',
        'Disallow: /admin/',
        'Disallow: /razbor/gotovo/',
        'Allow: /',
        f'Sitemap: {request.scheme}://{host}/sitemap.xml',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')


def sitemap_xml(request):
    paths = ['', 'razbor/', 'club/', 'privacy/']
    base = f'{request.scheme}://{request.get_host()}/'
    urls = ''.join(f'<url><loc>{base}{p}</loc></url>' for p in paths)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f'{urls}</urlset>')
    return HttpResponse(xml, content_type='application/xml')
