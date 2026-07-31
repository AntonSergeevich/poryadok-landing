# landing/views.py
import logging

from django.shortcuts import render, redirect
import pandas as pd
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# ID token и chat
TELEGRAM_BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = settings.TELEGRAM_CHAT_ID


def index(request):
    context = {'success': False, 'form_error': None}
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        area = request.POST.get('area', '').strip()

        if not name or len(phone) != 18:
            context['form_error'] = 'Проверьте имя и номер телефона — номер нужен целиком.'
        else:
            message_text = (
                f"ПОРЯДОК // ЗАЯВКА НА РАЗБОР\n"
                f"----------------------------------------\n"
                f"Имя: {name}\n"
                f"Телефон: {phone}\n"
                f"Сфера: {area or '—'}\n"
                f"----------------------------------------\n"
                f"Связаться сегодня, согласовать время разбора."
            )
            # Заявку показываем принятой в любом случае: посетитель свою часть выполнил.
            # Сбой доставки — наша проблема, поэтому он попадает в лог, а не в тишину.
            context['success'] = True
            try:
                response = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'text': message_text},
                    timeout=5
                )
                if response.status_code != 200:
                    logger.error(
                        'Заявка не доставлена в Telegram (HTTP %s): %s | %s',
                        response.status_code, name, phone
                    )
            except requests.exceptions.RequestException:
                logger.exception('Заявка не доставлена в Telegram: %s | %s', name, phone)

    return render(request, 'landing/index.html', context)


def express_audit(request):
    if request.method == 'POST' and request.FILES.get('sales_file'):
        uploaded_file = request.FILES['sales_file']
        phone = request.POST.get('phone', '').strip()

        try:
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file)

            df.columns = [str(col).strip().lower() for col in df.columns]

            col_sum = next((c for c in df.columns if 'сумма' in c or 'чек' in c or 'amount' in c), None)
            col_status = next((c for c in df.columns if 'статус' in c or 'этап' in c or 'status' in c), None)
            col_manager = next((c for c in df.columns if 'менеджер' in c or 'ответственный' in c), None)

            if not col_sum or not col_status:
                request.session[
                    'audit_error'] = 'Не удалось распознать колонки. Используйте заголовки: "Сумма", "Статус", "Менеджер".'
                return redirect('audit_result')

            df[col_sum] = pd.to_numeric(df[col_sum].astype(str).str.replace(r'[^\d.]', '', regex=True),
                                        errors='coerce').fillna(0)

            total_leads = len(df)
            won_mask = df[col_status].astype(str).str.contains('оплач|успеш|закрыт|продано', case=False, na=False)
            lost_mask = df[col_status].astype(str).str.contains('отказ|отмен|слив|потер|брак', case=False, na=False)

            total_revenue = float(df[won_mask][col_sum].sum())
            lost_revenue = float(df[lost_mask][col_sum].sum())
            conversion = round((won_mask.sum() / total_leads * 100), 1) if total_leads > 0 else 0

            top_loser = "Не определен"
            if col_manager and lost_mask.any():
                top_loser = str(df[lost_mask].groupby(col_manager)[col_sum].sum().idxmax())

            audit_metrics = {
                'total_leads': total_leads,
                'total_revenue': f"{total_revenue:,.0f}".replace(',', ' '),
                'lost_revenue': f"{lost_revenue:,.0f}".replace(',', ' '),
                'conversion': conversion,
                'top_loser': top_loser
            }

            # Отправка в Telegram. Имя файла и телефон приходят от пользователя —
            # шлём обычным текстом, чтобы разметку нельзя было подделать.
            tg_message = (
                f"ПОРЯДОК // ЭКСПРЕСС-ПРОВЕРКА\n"
                f"----------------------------------------\n"
                f"Телефон: {phone}\n"
                f"Файл: {uploaded_file.name}\n"
                f"Всего обращений: {total_leads}\n"
                f"Оплачено: {audit_metrics['total_revenue']} руб.\n"
                f"Не дошло до оплаты: {audit_metrics['lost_revenue']} руб.\n"
                f"Конверсия: {conversion}%\n"
                f"Больше всего потерь у: {top_loser}\n"
                f"----------------------------------------\n"
                f"Связаться и предложить разбор процессов."
            )
            try:
                response = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'text': tg_message},
                    timeout=5
                )
                if response.status_code != 200:
                    logger.error('Экспресс-проверка не доставлена в Telegram (HTTP %s): %s',
                                 response.status_code, phone)
            except requests.exceptions.RequestException:
                logger.exception('Экспресс-проверка не доставлена в Telegram: %s', phone)

            # Сохраняем результат в сессию и перенаправляем (GET запрос)
            request.session['audit_result'] = audit_metrics
            request.session.pop('audit_error', None)
            return redirect('audit_result')

        except Exception as e:
            request.session['audit_error'] = f'Ошибка при обработке файла: {str(e)}'
            return redirect('audit_result')

    return redirect('index')


# Отдельный View для отображения результатов (чистый GET)
def audit_result_view(request):
    result = request.session.get('audit_result')
    error = request.session.get('audit_error')

    # Если страница открыта напрямую без результатов — отправляем на главную
    if not result and not error:
        return redirect('index')

    return render(request, 'landing/audit_result.html', {'result': result, 'error': error})