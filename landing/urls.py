# landing/urls.py
from django.templatetags.static import static as static_url
from django.shortcuts import redirect
from django.urls import path

from . import views


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
