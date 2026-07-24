# landing/urls.py
from django.urls import path
from .views import index, express_audit, audit_result_view

urlpatterns = [
    path('', index, name='index'),
    path('express-audit/', express_audit, name='express_audit'),
    path('audit-result/', audit_result_view, name='audit_result'),
]