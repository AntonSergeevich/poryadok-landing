"""Представления сайта.

Правило одно: заявка сначала попадает в базу, и только потом мы пытаемся
что-то отправить в Telegram. Если Telegram лежит — заявка всё равно наша.
"""
import logging

import pandas as pd
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import AuditForm, ClubForm, LeadForm
from .models import Client, ClubSubscription, Lead, Payment, normalize_phone
from .services import payments as pay
from .services import telegram as tg

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

        if pay.is_enabled():
            client = _client_from_lead(lead)
            subscription = ClubSubscription.objects.create(
                client=client, plan=plan, price=CLUB_PRICES.get(plan, 0))
            payment = Payment.objects.create(
                client=client, amount=subscription.price,
                purpose=Payment.Purpose.CLUB, provider='yookassa',
                payer_phone=lead.phone, payer_telegram=lead.telegram_username)
            subscription.payment = payment
            subscription.save(update_fields=['payment', 'updated_at'])

            payment_id, confirmation_url = pay.create_payment(
                amount=subscription.price,
                description=f'Клуб «Порядок», {subscription.get_plan_display().lower()}',
                return_url=request.build_absolute_uri(reverse('club_done')),
                metadata={'payment_pk': str(payment.pk)},
            )
            if payment_id and confirmation_url:
                payment.provider_payment_id = payment_id
                payment.save(update_fields=['provider_payment_id', 'updated_at'])
                return redirect(confirmation_url)
            # Эквайринг не ответил — не теряем человека, ведём по обычному пути.
            logger.error('Не удалось создать платёж, заявка %s остаётся ручной', lead.pk)

        request.session['club_sent'] = True
        return redirect(reverse('club') + '?ok=1#join')

    if request.GET.get('ok') and request.session.pop('club_sent', False):
        success = True
        form = ClubForm()

    return render(request, 'landing/club.html', {
        'form': form,
        'success': success,
        'prices': {k: f'{v:,}'.replace(',', ' ') for k, v in CLUB_PRICES.items()},
        'payments_enabled': pay.is_enabled(),
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
    import json

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


def express_audit(request):
    """Быстрый расчёт по выгрузке продаж."""
    if request.method != 'POST':
        return redirect('index')

    form = AuditForm(request.POST, request.FILES)
    if not form.is_valid():
        first_error = next(iter(form.errors.values()))[0]
        request.session['audit_error'] = first_error
        return redirect('audit_result')

    uploaded = form.cleaned_data['sales_file']
    phone = form.cleaned_data['phone']

    try:
        if uploaded.name.lower().endswith('.csv'):
            df = pd.read_csv(uploaded)
        else:
            df = pd.read_excel(uploaded)

        df.columns = [str(col).strip().lower() for col in df.columns]
        col_sum = next((c for c in df.columns
                        if 'сумма' in c or 'чек' in c or 'amount' in c), None)
        col_status = next((c for c in df.columns
                           if 'статус' in c or 'этап' in c or 'status' in c), None)
        col_manager = next((c for c in df.columns
                            if 'менеджер' in c or 'ответственный' in c), None)

        if not col_sum or not col_status:
            request.session['audit_error'] = (
                'Не нашёл нужных столбцов. Назовите их «Сумма» и «Статус» — '
                'и загрузите файл заново.')
            return redirect('audit_result')

        df[col_sum] = pd.to_numeric(
            df[col_sum].astype(str).str.replace(r'[^\d.]', '', regex=True),
            errors='coerce').fillna(0)

        total_leads = len(df)
        won = df[col_status].astype(str).str.contains(
            'оплач|успеш|закрыт|продано', case=False, na=False)
        lost = df[col_status].astype(str).str.contains(
            'отказ|отмен|слив|потер|брак', case=False, na=False)

        metrics = {
            'total_leads': total_leads,
            'total_revenue': f'{float(df[won][col_sum].sum()):,.0f}'.replace(',', ' '),
            'lost_revenue': f'{float(df[lost][col_sum].sum()):,.0f}'.replace(',', ' '),
            'conversion': round(won.sum() / total_leads * 100, 1) if total_leads else 0,
            'top_loser': (str(df[lost].groupby(col_manager)[col_sum].sum().idxmax())
                          if col_manager and lost.any() else 'Не определён'),
        }
    except Exception:
        logger.exception('Не удалось разобрать файл %s', uploaded.name)
        request.session['audit_error'] = (
            'Файл не удалось прочитать. Проверьте, что это обычная таблица '
            'с заголовками в первой строке.')
        return redirect('audit_result')

    lead = Lead.objects.create(
        phone=phone, source=Lead.Source.AUDIT,
        comment=(f'Файл: {uploaded.name}. Обращений {metrics["total_leads"]}, '
                 f'конверсия {metrics["conversion"]}%, '
                 f'не дошло до оплаты {metrics["lost_revenue"]} ₽.'))
    try:
        lead.delivered_to_telegram = tg.notify_lead(lead)
        lead.save(update_fields=['delivered_to_telegram', 'updated_at'])
    except Exception:
        logger.exception('Не удалось уведомить о проверке %s', lead.pk)

    request.session['audit_result'] = metrics
    request.session.pop('audit_error', None)
    return redirect('audit_result')


def audit_result_view(request):
    result = request.session.pop('audit_result', None)
    error = request.session.pop('audit_error', None)
    if not result and not error:
        return redirect('index')
    return render(request, 'landing/audit_result.html', {'result': result, 'error': error})


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
        'Disallow: /audit-result/',
        'Allow: /',
        f'Sitemap: {request.scheme}://{host}/sitemap.xml',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')


def sitemap_xml(request):
    paths = ['', 'club/', 'privacy/']
    base = f'{request.scheme}://{request.get_host()}/'
    urls = ''.join(f'<url><loc>{base}{p}</loc></url>' for p in paths)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f'{urls}</urlset>')
    return HttpResponse(xml, content_type='application/xml')
