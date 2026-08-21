# landing/urls.py
from django.contrib.auth import views as auth_views
from django.templatetags.static import static as static_url
from django.shortcuts import redirect
from django.urls import path, reverse_lazy

from . import build_views, cabinet, contract_views, portfolio_views, views


def _icon(name):
    """Иконки, которые браузеры просят по корню сайта, минуя разметку.

    iOS Safari запрашивает apple-touch-icon сам, независимо от того, что
    написано в <head>. Без этих маршрутов каждый заход с айфона давал
    несколько 404 подряд — а такая серия выглядит как перебор адресов
    и может привести к блокировке адреса посетителя защитой сервера.

    Адрес вычисляется при обращении, а не при загрузке этого файла.
    Разница принципиальная: static() читает справочник отпечатков, и
    если справочника ещё нет — не собрана статика после обновления, —
    то при вычислении на этапе загрузки падает весь URLconf, а с ним и
    весь сайт. При вычислении в момент запроса страдает одна иконка.
    """
    def view(request, *args, **kwargs):
        return redirect(static_url(f'landing/img/{name}'), permanent=True)
    return view

urlpatterns = [
    path('', views.index, name='index'),
    path('club/', views.club, name='club'),
    path('club/done/', views.club_done, name='club_done'),
    path('club/telegram/', views.club_telegram, name='club_telegram'),
    path('privacy/', views.privacy, name='privacy'),
    path('razbor/', views.survey, name='survey'),
    path('raboty/<slug:slug>/', views.work, name='work'),
    path('sobrat/', views.constructor, name='constructor'),
    path('sobrat/schitat/', views.constructor_price, name='constructor_price'),

    # Кабинет. Одна дверь на двоих: развилка по роли внутри, а не два
    # адреса — ссылку отправляют в мессенджер, и открываться она обязана
    # у обоих.
    path('cabinet/', cabinet.home, name='cabinet'),
    path('cabinet/vhod/', auth_views.LoginView.as_view(
        template_name='landing/cabinet/login.html',
        redirect_authenticated_user=True), name='login'),
    # Выход только POST-ом — иначе кабинет закрывается от предзагрузки
    # ссылок браузером и от чужой картинки в переписке.
    path('cabinet/vyhod/', auth_views.LogoutView.as_view(), name='logout'),

    # Восстановление пароля. Пароль от кабинета, куда заходят раз в
    # неделю, будет забыт — это «когда», а не «если». Без самостоятельного
    # восстановления каждый такой случай превращается в звонок мне,
    # а моё время здесь самое дорогое.
    #
    # Стандартный путь Django: почта → письмо со ссылкой → новый пароль.
    # Ссылка живёт трое суток и срабатывает один раз.
    path('cabinet/parol/zabyl/', auth_views.PasswordResetView.as_view(
        template_name='landing/cabinet/reset.html',
        email_template_name='landing/cabinet/reset_email.txt',
        subject_template_name='landing/cabinet/reset_subject.txt',
        success_url=reverse_lazy('password_reset_done')), name='password_reset'),
    path('cabinet/parol/zabyl/gotovo/', auth_views.PasswordResetDoneView.as_view(
        template_name='landing/cabinet/reset_done.html'), name='password_reset_done'),
    path('cabinet/parol/novyy/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(
             template_name='landing/cabinet/reset_confirm.html',
             success_url=reverse_lazy('password_reset_complete')),
         name='password_reset_confirm'),
    path('cabinet/parol/novyy/gotovo/', auth_views.PasswordResetCompleteView.as_view(
        template_name='landing/cabinet/reset_complete.html'),
        name='password_reset_complete'),
    path('cabinet/moy-proekt/', cabinet.my_project, name='cabinet_mine'),
    path('cabinet/parol/', cabinet.password, name='cabinet_password'),
    # Разделы кабинета.
    path('cabinet/zayavki/', cabinet.leads, name='cabinet_leads'),
    path('cabinet/zayavki/<int:pk>/', cabinet.lead_detail, name='cabinet_lead'),
    path('cabinet/zayavki/<int:pk>/pravka/', cabinet.lead_update,
         name='cabinet_lead_update'),
    path('cabinet/zayavki/<int:pk>/status/', cabinet.lead_status,
         name='cabinet_lead_status'),
    path('cabinet/zayavki/<int:pk>/udalit/', cabinet.lead_delete,
         name='cabinet_lead_delete'),
    path('cabinet/zayavki/<int:pk>/proekt/', cabinet.lead_to_project,
         name='cabinet_lead_project'),
    path('cabinet/zayavki/<int:pk>/fayl/', cabinet.lead_file_add,
         name='cabinet_lead_file'),
    path('cabinet/zayavka-fayl/<int:pk>/udalit/', cabinet.lead_file_delete,
         name='cabinet_lead_file_delete'),
    # Конструктор в кабинете. Тот же, что на сайте: состав должен быть
    # одним списком на всём пути от заявки до Приложения № 1 к договору.
    path('cabinet/zayavki/<int:pk>/sobrat/', build_views.lead_build,
         name='cabinet_lead_build_page'),
    path('cabinet/zayavki/<int:pk>/sobrat/zapisat/', build_views.lead_build_save,
         name='cabinet_lead_build'),
    path('cabinet/zakazchiki/', cabinet.clients, name='cabinet_clients'),
    path('cabinet/dengi/', cabinet.money, name='cabinet_money'),
    path('cabinet/svodka/', cabinet.summary, name='cabinet_summary'),

    # Портфолио. Работы заводятся здесь, а не правкой кода: иначе работа
    # не добавляется никогда — она откладывается до следующего раза,
    # когда я и так буду в коде.
    path('cabinet/portfolio/', portfolio_views.works, name='cabinet_works'),
    path('cabinet/portfolio/novaya/', portfolio_views.work_create,
         name='cabinet_work_create'),
    path('cabinet/portfolio/<int:pk>/', portfolio_views.work_detail,
         name='cabinet_work'),
    path('cabinet/portfolio/<int:pk>/pravka/', portfolio_views.work_update,
         name='cabinet_work_update'),
    path('cabinet/portfolio/<int:pk>/pokazat/', portfolio_views.work_publish,
         name='cabinet_work_publish'),
    path('cabinet/portfolio/<int:pk>/udalit/', portfolio_views.work_delete,
         name='cabinet_work_delete'),
    path('cabinet/portfolio/<int:pk>/chislo/', portfolio_views.fact_add,
         name='cabinet_work_fact'),
    path('cabinet/chislo/<int:pk>/udalit/', portfolio_views.fact_delete,
         name='cabinet_work_fact_delete'),
    path('cabinet/portfolio/<int:pk>/snimok/', portfolio_views.shot_add,
         name='cabinet_work_shot'),
    path('cabinet/snimok/<int:pk>/udalit/', portfolio_views.shot_delete,
         name='cabinet_work_shot_delete'),

    path('cabinet/proekt/<int:pk>/', cabinet.project_detail, name='cabinet_project'),
    path('cabinet/proekt/<int:pk>/etapy/', cabinet.stages_build,
         name='cabinet_stages_build'),
    path('cabinet/proekt/<int:pk>/oplata/', cabinet.payment_add,
         name='cabinet_payment_add'),
    path('cabinet/proekt/<int:pk>/sobrat/', build_views.project_build,
         name='cabinet_project_build_page'),
    path('cabinet/proekt/<int:pk>/sobrat/zapisat/',
         build_views.project_build_save, name='cabinet_project_build'),
    path('cabinet/proekt/<int:pk>/fayl/', cabinet.project_file_add,
         name='cabinet_project_file'),
    path('cabinet/fayl/<int:pk>/udalit/', cabinet.project_file_delete,
         name='cabinet_project_file_delete'),
    path('cabinet/proekt/<int:pk>/summa/', cabinet.project_price,
         name='cabinet_project_price'),
    path('cabinet/oplata/<int:pk>/udalit/', cabinet.payment_delete,
         name='cabinet_payment_delete'),
    path('cabinet/proekt/novyy/', cabinet.project_create,
         name='cabinet_project_create'),

    # Договор. Собирается по проекту, живёт своей страницей: её
    # открывают, чтобы прочитать и распечатать, а не чтобы работать.
    path('cabinet/proekt/<int:pk>/dogovor/', contract_views.contract_create,
         name='cabinet_contract_create'),
    path('cabinet/dogovor/<int:pk>/', contract_views.contract_page,
         name='cabinet_contract'),
    path('cabinet/dogovor/<int:pk>/pravka/', contract_views.contract_update,
         name='cabinet_contract_update'),
    path('cabinet/dogovor/<int:pk>/rekvizity/', contract_views.client_requisites,
         name='cabinet_contract_requisites'),
    path('cabinet/dogovor/<int:pk>/vystavit/', contract_views.contract_issue,
         name='cabinet_contract_issue'),
    path('cabinet/dogovor/<int:pk>/otmenit/', contract_views.contract_cancel,
         name='cabinet_contract_cancel'),
    path('cabinet/dogovor/<int:pk>/podpisan/', contract_views.contract_sign,
         name='cabinet_contract_sign'),
    path('cabinet/klient/<int:client_pk>/dostup/', cabinet.grant_access,
         name='cabinet_grant'),
    path('cabinet/klient/<int:client_pk>/dostup/zakryt/', cabinet.revoke_access,
         name='cabinet_revoke'),
    path('cabinet/zadacha/<int:pk>/otmetit/', cabinet.task_toggle,
         name='cabinet_task_toggle'),
    path('cabinet/etap/<int:stage_pk>/zadacha/', cabinet.task_add,
         name='cabinet_task_add'),
    path('cabinet/zadacha/<int:pk>/udalit/', cabinet.task_delete,
         name='cabinet_task_delete'),
    path('cabinet/etap/<int:pk>/status/', cabinet.stage_status,
         name='cabinet_stage_status'),
    path('cabinet/proekt/<int:pk>/pismo/', cabinet.chat_send,
         name='cabinet_chat_send'),
    path('cabinet/soobshchenie/<int:pk>/pravka/', cabinet.chat_edit,
         name='cabinet_chat_edit'),
    path('cabinet/proekt/<int:pk>/pisma/', cabinet.chat_since,
         name='cabinet_chat_since'),
    path('cabinet/proekt/<int:pk>/pisma/ranshe/', cabinet.chat_older,
         name='cabinet_chat_older'),
    path('razbor/gotovo/', views.survey_done, name='survey_done'),
    path('pay/yookassa/webhook/', views.yookassa_webhook, name='yookassa_webhook'),
    path('pay/getplatinum/webhook/', views.getplatinum_webhook, name='getplatinum_webhook'),
    # Секрет в адресе — первая из двух проверок подлинности.
    path('tg/<str:secret>/', views.telegram_bot_webhook, name='telegram_bot_webhook'),
    path('robots.txt', views.robots_txt, name='robots'),
    path('sitemap.xml', views.sitemap_xml, name='sitemap'),

    path('favicon.ico', _icon('favicon.ico')),
    path('apple-touch-icon.png', _icon('apple-touch-icon.png')),
    path('apple-touch-icon-precomposed.png', _icon('apple-touch-icon.png')),
    path('apple-touch-icon-180x180.png', _icon('apple-touch-icon.png')),
    path('apple-touch-icon-180x180-precomposed.png', _icon('apple-touch-icon.png')),
    path('apple-touch-icon-152x152.png', _icon('apple-touch-icon.png')),
    path('apple-touch-icon-152x152-precomposed.png', _icon('apple-touch-icon.png')),
    path('apple-touch-icon-120x120.png', _icon('apple-touch-icon.png')),
    path('apple-touch-icon-120x120-precomposed.png', _icon('apple-touch-icon.png')),
]
