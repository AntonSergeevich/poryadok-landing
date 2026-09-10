"""Представления сайта.

Правило одно: заявка сначала попадает в базу, и только потом мы пытаемся
что-то отправить в Telegram. Если Telegram лежит — заявка всё равно наша.
"""
import hmac
import json
import logging

from django.conf import settings
from django.db import transaction
from django.http import (Http404, HttpResponse, HttpResponseBadRequest,
                         JsonResponse)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import ClubForm, LeadForm, SurveyForm
from .models import (Client, Lead, Payment, Survey, Work,
                     normalize_phone)
from .services import club as club_service
from .services import getplatinum as gp
from .services import payments as pay
from .services import telegram as tg
from . import survey as survey_logic
from .survey import QUESTIONS
from . import constructor as build
from . import contract
# Работы живут в базе и заводятся из кабинета. Модуль landing/works.py
# остался источником переноса (миграция 0010) — из кода их больше
# не читают.

logger = logging.getLogger(__name__)

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
        'works': published_works(),
        # Первый вопрос разбора задаётся прямо здесь, на первом экране.
        # Берём его из тех же данных, что и сам разбор: два списка
        # вариантов разошлись бы в первый же месяц.
        'first_question': survey_logic.first_question(),
        # Лестница цен считается тем же кодом, что и конструктор.
        'ladder': build.ladder(),
        'total': _asked_count(),
        'total_word': contract.words(_asked_count()),
    })


def club(request):
    """Закрытый клуб. Вход свободный: заявка нужна, чтобы выдать ссылку.

    Ссылку показываем прямо на странице, а не только шлём в бот: человек
    заполнил форму и ждёт, и отправлять его за результатом в другое место
    — лишний шаг, на котором часть людей теряется.
    """
    form = ClubForm(request.POST or None)
    success = False
    invite_link = ''

    if request.method == 'POST' and form.is_valid():
        lead = _save_lead(form, Lead.Source.CLUB, comment='Свободный вход')
        subscription = club_service.open_free_access(_client_from_lead(lead))
        request.session['club_sent'] = True
        request.session['club_invite'] = subscription.invite_link
        return redirect(reverse('club') + '?ok=1#join')

    if request.GET.get('ok') and request.session.pop('club_sent', False):
        success = True
        invite_link = request.session.pop('club_invite', '')
        form = ClubForm()

    return render(request, 'landing/club.html', {
        'form': form,
        'success': success,
        'invite_link': invite_link,
    })


@csrf_exempt
@require_POST
def telegram_bot_webhook(request, secret):
    """Сообщения, приходящие боту.

    Заменяет виджет входа на сайте: Telegram объявил его устаревшим, и
    его страница авторизации отвечает «deprecated». Здесь всё происходит
    внутри Telegram, сторонние скрипты в браузере не участвуют — а с
    учётом здешних проблем с фильтрацией трафика это ещё и надёжнее.

    Смысл ровно один: узнать числовой идентификатор человека. По нику
    Telegram не даёт ни писать, ни исключать из канала; по номеру
    телефона мы находим его у себя, потому что номер он оставлял
    при оплате.

    Подлинность запроса проверяется дважды: секрет в адресе и секрет
    в заголовке. Оба ставит Telegram при подписке на сообщения.
    """
    expected = getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '')
    # Сравниваем байты, а не строки: compare_digest со строками падает на
    # любом символе вне ASCII. В секрете вполне может оказаться кириллица,
    # а в адресе — что угодно от постороннего. Ошибка 500 вместо отказа
    # тут ни к чему.
    if not expected \
            or not hmac.compare_digest(secret.encode(), expected.encode()) \
            or not hmac.compare_digest(
                request.headers.get('X-Telegram-Bot-Api-Secret-Token', '').encode(),
                expected.encode()):
        logger.warning('Бот: запрос с неверным секретом')
        return HttpResponseBadRequest('nope')

    try:
        update = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest('bad json')

    message = update.get('message') or {}
    chat_id = (message.get('chat') or {}).get('id')
    if not chat_id:
        return JsonResponse({'ok': True})

    contact = message.get('contact')
    if contact:
        _bot_link_by_phone(chat_id, contact, message.get('from') or {})
        return JsonResponse({'ok': True})

    text = (message.get('text') or '').strip()
    if text.startswith('/start'):
        tg.ask_contact(chat_id,
                       'Здравствуйте! Это бот клуба «Порядок».\n\n'
                       'Чтобы я мог прислать ссылку на вход и предупреждать '
                       'об окончании доступа, поделитесь номером телефона — '
                       'тем же, что указывали при оплате.\n\n'
                       'Нажмите кнопку ниже, вводить ничего не нужно.')
        return JsonResponse({'ok': True})

    tg.reply(chat_id,
             'Чтобы получить доступ в клуб, отправьте команду /start '
             'и поделитесь номером телефона.')
    return JsonResponse({'ok': True})


def _bot_link_by_phone(chat_id, contact, sender):
    """Связывает пришедший номер с клиентом и выдаёт доступ."""
    # Telegram отдаёт user_id вместе с контактом только если человек
    # поделился СВОИМ номером. Чужую карточку подсунуть можно, но там
    # user_id не будет — значит и связывать нечего.
    if contact.get('user_id') != sender.get('id'):
        tg.reply(chat_id, 'Поделитесь, пожалуйста, своим номером — '
                          'кнопкой «Поделиться номером».')
        return

    phone = normalize_phone(contact.get('phone_number') or '')
    client = Client.objects.filter(phone=phone).first()
    if client is None:
        tg.reply(chat_id,
                 'По этому номеру оплаты не нашёл.\n\n'
                 'Если вы оплачивали с другого номера — напишите его '
                 f'мне на {settings.SITE_PHONE_PRETTY}, разберусь вручную.')
        return

    user_id = sender.get('id')
    username = (sender.get('username') or '').lstrip('@')
    fields = []
    if client.telegram_user_id != user_id:
        client.telegram_user_id = user_id
        fields.append('telegram_user_id')
    if username and client.telegram_username != username:
        client.telegram_username = username
        fields.append('telegram_username')
    if fields:
        client.save(update_fields=fields + ['updated_at'])
    logger.info('Бот: клиент %s связан с telegram id %s', client.pk, user_id)

    subscription = client.club_subscriptions.running().by_freshness().first()

    if subscription is None:
        tg.reply(chat_id,
                 f'Узнал вас, {client.name}. Доступа в клуб пока нет.\n\n'
                 'Вход свободный, займёт минуту:\n' + club_service.club_url())
        return

    if not subscription.invite_link:
        link = tg.create_club_invite(name_hint=client.name)
        if link:
            subscription.invite_link = link
            subscription.invite_sent_at = timezone.now()
            subscription.save(update_fields=['invite_link', 'invite_sent_at',
                                             'updated_at'])

    if subscription.invite_link:
        # У свободного входа срока нет, и обещать напоминание не о чем.
        term = ('Доступ бессрочный.' if subscription.is_endless
                else f'Доступ до {subscription.ends_at:%d.%m.%Y}. '
                     'За три дня до окончания я напомню.')
        tg.reply(chat_id,
                 f'Готово, {client.name}. {term}\n\n'
                 'Ссылка на вход в канал — одноразовая, работает только '
                 'на ваш аккаунт:\n' + subscription.invite_link)
    else:
        tg.reply(chat_id,
                 'Узнал вас, подписка активна, но ссылку создать не вышло. '
                 f'Напишите или позвоните: {settings.SITE_PHONE_PRETTY}')
        tg.notify(f'ПОРЯДОК // КЛУБ\nНе выдалась ссылка для {client.name} '
                  f'({client.phone_pretty}) — выдайте вручную.')


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
        subscription = client.club_subscriptions.running().by_freshness().first()

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
            club_service.grant_access(payment)
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

    signed = gp.verify(body)
    if not signed and gp.checksum_required():
        return HttpResponseBadRequest('bad checksum')

    deal_id = body.get('dealId')
    # Пишем в журнал каждое уведомление — и удачное тоже. Пока подпись
    # совещательная, это единственный способ узнать, сходится ли она.
    logger.info('GetPlatinum: уведомление по заказу %s, подпись %s',
                deal_id, 'сошлась' if signed else 'НЕ СОШЛАСЬ')
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
            club_service.grant_access(payment)
    else:
        payment.status = Payment.Status.CANCELED
        payment.save(update_fields=['status', 'updated_at'])
        logger.info('GetPlatinum: заказ %s не оплачен', deal_id)

    return JsonResponse({'ok': True})


def survey(request):
    """Разбор процессов: тест из landing/survey.py.

    Работает и без JS: тогда это одна длинная форма. Со скриптом вопросы
    показываются по одному. Контакт необязателен — человек имеет право
    просто посмотреть на себя со стороны, и это честнее, чем держать
    результат в заложниках.
    """
    form = SurveyForm(request.POST or None)

    # Ответ на первый вопрос мог прийти с главной — там его и задают.
    preset = ''
    if not form.is_bound:
        first_id = survey_logic.first_question()['id']
        value = request.GET.get(first_id, '')
        preset = value if survey_logic.valid_answer(first_id, value) else ''

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
        'steps': _survey_steps(form, preset),
        # Первый вопрос уже отвечен — скрипту начинать со второго. Без
        # скрипта шагов нет, форма одна, и начинать неоткуда.
        'start_at': 1 if preset else 0,
        'total_word': contract.words(_asked_count()),
        # Сколько вопросов человек увидит на самом деле. Условные из одной
        # пары исключают друг друга, поэтому в счёт идёт один: обещать
        # семнадцать и показать шестнадцать — мелкое, но враньё.
        'total': _asked_count(),
    })


def _asked_count():
    seen = set()
    total = 0
    for q in QUESTIONS:
        rule = q.get('show_if')
        if rule is None:
            total += 1
        elif rule[0] not in seen:
            seen.add(rule[0])
            total += 1
    return total


def _survey_steps(form, preset=''):
    """Готовит вопросы к отрисовке.

    Разметку вариантов пишем руками, а не через {{ field }}, — значит
    отметку «выбрано» надо посчитать здесь. В шаблоне логике не место.

    `preset` — ответ на первый вопрос, пришедший с главной. Он именно
    отмечается в форме, а не запоминается отдельно: человек должен
    видеть, что за него отмечено, и мочь это поменять.
    """
    data = form.data if form.is_bound else {}
    first_id = survey_logic.first_question()['id']
    steps = []
    for q in QUESTIONS:
        chosen = data.getlist(q['id']) if hasattr(data, 'getlist') else []
        if preset and q['id'] == first_id and not form.is_bound:
            chosen = [preset]
        options = [{
            'value': o['value'],
            'label': o['label'],
            'checked': o['value'] in chosen,
        } for o in q.get('options', [])]
        if q.get('other'):
            options.append({'value': 'other', 'label': 'Другое',
                            'checked': 'other' in chosen, 'is_other': True})
        # Условие показа — в разметку строкой «вопрос=ответ». Скрипт прячет
        # такие шаги, пока ответ не совпал; без скрипта они просто видны все,
        # и это правильно — спрятать вопрос нечем, а посчитается всё равно
        # только подходящий.
        rule = q.get('show_if')
        steps.append({
            'q': q,
            'show_if': f'{rule[0]}={rule[1]}' if rule else '',
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

    diagnosis = entry.diagnose()
    # Кладём подсказку в сессию, а не в адрес: список блоков в ссылке
    # выглядел бы как подсунутый, а он именно тот, что я назвал бы
    # голосом, посмотрев на ответы.
    request.session['build_suggest'] = build.suggest(diagnosis)

    return render(request, 'landing/survey_done.html', {
        'entry': entry,
        'result': diagnosis,
        'left_contact': bool(entry.phone),
    })


def constructor(request):
    """Конструктор: человек собирает состав системы сам и видит цену.

    Вопрос «сколько стоит» без конструктора имеет два плохих ответа:
    «от 50 000» — и человек уходит, не поняв, что входит; «договорная» —
    и уходит, решив, что дорого. Здесь он отвечает себе сам, и после
    этого «почему так дорого» уже не спрашивает.

    Считает тот же код, что и при отправке: расхождение между «посчитал
    сайт» и «назвал я» — ровно тот разговор, от которого система
    избавляет.
    """
    form = LeadForm(request.POST or None)
    success = False

    if request.method == 'POST':
        picked = build.clean(request.POST.getlist('blocks'))
        scale_id = request.POST.get('scale') or 'solo'
        if form.is_valid():
            result = build.estimate(picked, scale_id)
            _save_lead(form, Lead.Source.BUILD,
                       comment=build.as_text(result['ids'], scale_id,
                                             result['total']))
            request.session['build_sent'] = True
            return redirect(reverse('constructor') + '?ok=1#cta')
    else:
        # Пришёл с разбора — отмечаем то, что закрывает найденные боли.
        # Не «побольше»: блок без совпадения остаётся выключенным.
        picked = request.session.pop('build_suggest', None) or build.core_ids()
        scale_id = 'solo'

    if request.GET.get('ok') and request.session.pop('build_sent', False):
        success = True
        form = LeadForm()

    result = build.estimate(picked, scale_id)
    return render(request, 'landing/constructor.html', {
        'form': form,
        'success': success,
        'blocks': build.BLOCKS,
        'scales': build.SCALES,
        'picked': result['ids'],
        'scale_id': result['scale']['id'],
        'result': result,
    })


@require_POST
def constructor_price(request):
    """Пересчёт состава без перезагрузки.

    Считает и рисует сервер. Копия формулы на JavaScript однажды
    разойдётся с настоящей — и человек увидит одно число, а в заявке
    приедет другое.
    """
    result = build.estimate(request.POST.getlist('blocks'),
                            request.POST.get('scale') or 'solo')
    return JsonResponse({
        'ok': True,
        'ids': result['ids'],
        'total': result['total'],
        # Готовая разметка, а не набор цифр. Собирать итог вторым кодом
        # в браузере уже пробовали: строка про скидку не появлялась
        # вовсе — её не было в разметке, а дорисовывать её скрипт
        # не умел. Пока итог собирается в двух местах, расхождения
        # будут возвращаться.
        'html': render_to_string('landing/_total.html', {'result': result},
                                 request=request),
    })

def work(request, slug):
    """Отдельная страница работы.

    На главной работы стоят короткими карточками: список работ должен
    читаться за десять секунд, а не за пять экранов. Всё подробное —
    было, стало, проверяемые числа и снимки — живёт здесь, и сюда
    приходят те, кому это правда интересно.
    """
    item = get_object_or_404(published_works(), slug=slug)
    return render(request, 'landing/work.html', {
        'work': item,
        # Соседняя работа — чтобы со страницы был выход не только назад.
        'others': published_works().exclude(pk=item.pk),
    })


def published_works():
    """Работы для сайта — одним заходом в базу.

    Снятая с публикации работа исчезает и с главной, и со своей страницы,
    и из карты сайта. Три места, и забыть про третье проще всего:
    поисковик продолжит водить людей на страницу, которой уже нет.
    """
    return (Work.objects.filter(is_published=True)
            .prefetch_related('fact_rows', 'shot_rows'))

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
    paths = ['', 'razbor/', 'sobrat/', 'club/', 'privacy/']
    paths += [f'raboty/{slug}/' for slug in
              published_works().values_list('slug', flat=True)]
    base = f'{request.scheme}://{request.get_host()}/'
    urls = ''.join(f'<url><loc>{base}{p}</loc></url>' for p in paths)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f'{urls}</urlset>')
    return HttpResponse(xml, content_type='application/xml')
