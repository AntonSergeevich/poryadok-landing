"""CRM внутри админки Django.

Отдельный интерфейс здесь не нужен: списки, фильтры, поиск и массовые
действия уже есть. Задача — настроить их так, чтобы работа шла прямо из
списка, без захода в каждую карточку.
"""
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from .models import Client, ClubSubscription, Lead, Payment, Project, format_phone
from .services import telegram as tg

admin.site.site_header = 'Порядок — рабочий стол'
admin.site.site_title = 'Порядок'
admin.site.index_title = 'Заявки, клиенты, оплаты'


def _phone_link(phone):
    if not phone:
        return '—'
    return format_html('<a href="tel:{}">{}</a>', phone, format_phone(phone))


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ('created_short', 'name', 'phone_link', 'area',
                    'source', 'status', 'delivered_icon', 'client')
    list_display_links = ('name',)
    list_editable = ('status',)
    list_filter = ('status', 'source', 'delivered_to_telegram', 'created_at')
    search_fields = ('name', 'phone', 'area', 'telegram_username', 'comment')
    date_hierarchy = 'created_at'
    autocomplete_fields = ('client',)
    readonly_fields = ('created_at', 'updated_at', 'delivered_to_telegram')
    list_per_page = 50
    actions = ('mark_contacted', 'make_clients')

    fieldsets = (
        ('Заявка', {'fields': ('name', 'phone', 'area', 'telegram_username',
                               'source', 'comment')}),
        ('Работа с заявкой', {'fields': ('status', 'client')}),
        ('Служебное', {'fields': ('delivered_to_telegram', 'created_at', 'updated_at'),
                       'classes': ('collapse',)}),
    )

    @admin.display(description='когда', ordering='created_at')
    def created_short(self, obj):
        return timezone.localtime(obj.created_at).strftime('%d.%m %H:%M')

    @admin.display(description='телефон')
    def phone_link(self, obj):
        return _phone_link(obj.phone)

    @admin.display(description='в TG', boolean=True)
    def delivered_icon(self, obj):
        return obj.delivered_to_telegram

    @admin.action(description='Отметить: связались')
    def mark_contacted(self, request, queryset):
        updated = queryset.update(status=Lead.Status.CONTACTED)
        self.message_user(request, f'Отмечено заявок: {updated}.')

    @admin.action(description='Завести клиента из заявки')
    def make_clients(self, request, queryset):
        created = linked = 0
        for lead in queryset:
            if lead.client_id:
                continue
            client, is_new = Client.objects.get_or_create(
                phone=lead.phone,
                defaults={'name': lead.name or 'Без имени', 'area': lead.area,
                          'telegram_username': lead.telegram_username})
            lead.client = client
            lead.status = Lead.Status.WON
            lead.save(update_fields=['client', 'status', 'updated_at'])
            created += int(is_new)
            linked += 1
        self.message_user(request,
                          f'Связано заявок: {linked}, новых клиентов: {created}.')


class ProjectInline(admin.TabularInline):
    model = Project
    extra = 0
    fields = ('title', 'price', 'status', 'started_at', 'launched_at')


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ('amount', 'purpose', 'status', 'provider', 'paid_at')


class SubscriptionInline(admin.TabularInline):
    model = ClubSubscription
    extra = 0
    fields = ('plan', 'status', 'price', 'ends_at', 'invite_link')
    readonly_fields = ('invite_link',)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'area', 'phone_link', 'telegram',
                    'club_badge', 'is_active')
    list_filter = ('is_active', 'city', 'created_at')
    search_fields = ('name', 'company', 'phone', 'email', 'telegram_username')
    inlines = (ProjectInline, PaymentInline, SubscriptionInline)
    actions = ('give_club_access',)
    list_per_page = 50

    @admin.display(description='телефон')
    def phone_link(self, obj):
        return _phone_link(obj.phone)

    @admin.display(description='telegram')
    def telegram(self, obj):
        if not obj.telegram_username:
            return '—'
        return format_html('<a href="https://t.me/{0}" target="_blank">@{0}</a>',
                           obj.telegram_username)

    @admin.display(description='клуб')
    def club_badge(self, obj):
        return '● активен' if obj.club_is_active else '—'

    @admin.action(description='Открыть доступ в клуб на год (бесплатно)')
    def give_club_access(self, request, queryset):
        opened = 0
        for client in queryset:
            if not client.telegram_username:
                self.message_user(
                    request, f'{client.name}: не заполнен ник в Telegram — пропустил.',
                    level=messages.WARNING)
                continue
            subscription = ClubSubscription.objects.create(
                client=client, plan=ClubSubscription.Plan.GIFT, price=0)
            subscription.activate()
            link = tg.create_club_invite(name_hint=client.name)
            if link:
                subscription.invite_link = link
                subscription.invite_sent_at = timezone.now()
                subscription.save(update_fields=['invite_link', 'invite_sent_at',
                                                 'updated_at'])
                opened += 1
            else:
                self.message_user(
                    request,
                    f'{client.name}: доступ включён, но ссылку создать не вышло — '
                    'проверьте TELEGRAM_CLUB_CHAT_ID.', level=messages.WARNING)
        if opened:
            self.message_user(request,
                              f'Доступ открыт: {opened}. Ссылки — в карточках клиентов.')


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('title', 'client', 'status', 'price', 'paid_column', 'debt_column',
                    'started_at', 'launched_at')
    list_filter = ('status', 'started_at')
    search_fields = ('title', 'client__name', 'client__phone')
    autocomplete_fields = ('client',)
    inlines = (PaymentInline,)

    @admin.display(description='оплачено')
    def paid_column(self, obj):
        return f'{obj.paid_total:.0f} ₽'

    @admin.display(description='остаток')
    def debt_column(self, obj):
        return f'{obj.debt:.0f} ₽'


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('created_short', 'amount_column', 'purpose', 'status',
                    'provider', 'client', 'project')
    list_editable = ('status',)
    list_filter = ('status', 'purpose', 'provider', 'created_at')
    search_fields = ('client__name', 'client__phone', 'payer_phone',
                     'payer_telegram', 'provider_payment_id', 'note')
    date_hierarchy = 'created_at'
    autocomplete_fields = ('client', 'project')
    readonly_fields = ('provider_payment_id', 'created_at', 'updated_at')

    @admin.display(description='когда', ordering='created_at')
    def created_short(self, obj):
        return timezone.localtime(obj.created_at).strftime('%d.%m.%Y %H:%M')

    @admin.display(description='сумма', ordering='amount')
    def amount_column(self, obj):
        return f'{obj.amount:.0f} ₽'


@admin.register(ClubSubscription)
class ClubSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('client', 'plan', 'status', 'price', 'ends_column',
                    'days_left_column', 'invite_column')
    list_filter = ('status', 'plan')
    search_fields = ('client__name', 'client__phone', 'client__telegram_username')
    autocomplete_fields = ('client',)
    readonly_fields = ('invite_link', 'invite_sent_at', 'created_at', 'updated_at')
    actions = ('extend_subscription', 'issue_invite')

    @admin.display(description='действует до', ordering='ends_at')
    def ends_column(self, obj):
        return timezone.localtime(obj.ends_at).strftime('%d.%m.%Y') if obj.ends_at else '—'

    @admin.display(description='осталось дней')
    def days_left_column(self, obj):
        left = obj.days_left
        return '—' if left is None else left

    @admin.display(description='приглашение')
    def invite_column(self, obj):
        if not obj.invite_link:
            return '—'
        return format_html('<a href="{}" target="_blank">ссылка</a>', obj.invite_link)

    @admin.action(description='Продлить по тарифу')
    def extend_subscription(self, request, queryset):
        count = queryset.count()
        for subscription in queryset:
            subscription.activate()
        self.message_user(request, f'Продлено подписок: {count}.')

    @admin.action(description='Выдать новую ссылку-приглашение')
    def issue_invite(self, request, queryset):
        issued = 0
        for subscription in queryset:
            link = tg.create_club_invite(name_hint=subscription.client.name)
            if link:
                subscription.invite_link = link
                subscription.invite_sent_at = timezone.now()
                subscription.save(update_fields=['invite_link', 'invite_sent_at',
                                                 'updated_at'])
                issued += 1
        if issued:
            self.message_user(request, f'Создано ссылок: {issued}.')
        else:
            self.message_user(request,
                              'Ни одной ссылки создать не вышло — проверьте настройки бота.',
                              level=messages.ERROR)
