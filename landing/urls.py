# landing/urls.py
from django.contrib.auth import views as auth_views
from django.templatetags.static import static as static_url
from django.shortcuts import redirect
from django.urls import path, reverse_lazy

from . import cabinet, views


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
    path('cabinet/proekt/<int:pk>/', cabinet.project_detail, name='cabinet_project'),
    path('cabinet/proekt/novyy/', cabinet.project_create,
         name='cabinet_project_create'),
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
