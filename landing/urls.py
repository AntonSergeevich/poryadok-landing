# landing/urls.py
from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('club/', views.club, name='club'),
    path('club/done/', views.club_done, name='club_done'),
    path('privacy/', views.privacy, name='privacy'),
    path('express-audit/', views.express_audit, name='express_audit'),
    path('audit-result/', views.audit_result_view, name='audit_result'),
    path('pay/yookassa/webhook/', views.yookassa_webhook, name='yookassa_webhook'),
    path('robots.txt', views.robots_txt, name='robots'),
    path('sitemap.xml', views.sitemap_xml, name='sitemap'),
]
