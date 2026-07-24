# landing/views.py
from django.shortcuts import render, redirect
import pandas as pd
import requests
from django.conf import settings

# ID token и chat
TELEGRAM_BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = settings.TELEGRAM_CHAT_ID


def index(request):
    context = {'success': False}
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()

        if name and len(phone) == 18:
            message_text = (
                f"🚨 СИСТЕМЫ ПОРЯДОК // НОВАЯ ЗАЯВКА\n"
                f"----------------------------------------\n"
                f"👤 Имя лида: {name}\n"
                f"📞 Телефон: {phone}\n"
                f"----------------------------------------\n"
                f"⚡️ Действие: Требуется связаться за 15 минут."
            )
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'text': message_text, 'parse_mode': 'HTML'},
                    timeout=5
                )
                context['success'] = True
            except requests.exceptions.RequestException:
                pass

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

            # Отправка в Telegram
            tg_message = (
                f"📊 <b>СИСТЕМЫ ПОРЯДОК // ЭКСПРЕСС-АУДИТ</b>\n"
                f"----------------------------------------\n"
                f"📞 <b>Телефон:</b> {phone}\n"
                f"📁 <b>Файл:</b> {uploaded_file.name}\n"
                f"📈 <b>Всего лидов:</b> {total_leads}\n"
                f"💰 <b>Выручка:</b> {audit_metrics['total_revenue']} руб.\n"
                f"🔴 <b>Упущенная прибыль:</b> {audit_metrics['lost_revenue']} руб.\n"
                f"🎯 <b>Конверсия:</b> {conversion}%\n"
                f"👤 <b>Лидер по потерям:</b> {top_loser}\n"
                f"----------------------------------------\n"
                f"🔥 <b>Действие:</b> Срочно звонить и закрывать на полноценный аудит."
            )
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'text': tg_message, 'parse_mode': 'HTML'},
                    timeout=5
                )
            except requests.exceptions.RequestException:
                pass

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