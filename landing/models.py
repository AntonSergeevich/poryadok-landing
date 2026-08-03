"""Модели CRM: заявки, клиенты, проекты, оплаты, подписка на закрытый клуб.

Смысл этих моделей — чтобы ни одна заявка и ни одна оплата не жили
в переписке и в голове. Ровно то, что мы продаём клиентам.
"""
import re

from django.db import models
from django.utils import timezone


def normalize_phone(raw):
    """+7 (995) 441-20-21 / 89954412021 / 79954412021 -> +79954412021."""
    digits = re.sub(r'\D', '', raw or '')
    if len(digits) == 11 and digits[0] in '78':
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    else:
        return (raw or '').strip()
    return '+' + digits


def format_phone(normalized):
    """+79954412021 -> +7 (995) 441-20-21. Непонятное возвращаем как есть."""
    d = re.sub(r'\D', '', normalized or '')
    if len(d) != 11:
        return normalized or ''
    return f'+{d[0]} ({d[1:4]}) {d[4:7]}-{d[7:9]}-{d[9:11]}'


class TimeStamped(models.Model):
    created_at = models.DateTimeField('создана', auto_now_add=True)
    updated_at = models.DateTimeField('изменена', auto_now=True)

    class Meta:
        abstract = True


class Client(TimeStamped):
    """Тот, с кем мы уже работаем или работали."""

    name = models.CharField('имя', max_length=120)
    company = models.CharField('компания', max_length=160, blank=True)
    area = models.CharField('сфера', max_length=120, blank=True)
    phone = models.CharField('телефон', max_length=32, db_index=True)
    email = models.EmailField('почта', blank=True)
    city = models.CharField('город', max_length=80, blank=True, default='Красноярск')
    telegram_username = models.CharField(
        'telegram', max_length=64, blank=True,
        help_text='Без @. Нужен, чтобы выдать доступ в закрытый клуб.')
    telegram_user_id = models.BigIntegerField('telegram ID', null=True, blank=True)
    note = models.TextField('заметки', blank=True)
    is_active = models.BooleanField('активный', default=True)

    class Meta:
        verbose_name = 'клиент'
        verbose_name_plural = 'клиенты'
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.name} · {format_phone(self.phone)}'

    def save(self, *args, **kwargs):
        self.phone = normalize_phone(self.phone)
        self.telegram_username = self.telegram_username.lstrip('@').strip()
        super().save(*args, **kwargs)

    @property
    def phone_pretty(self):
        return format_phone(self.phone)

    @property
    def club_is_active(self):
        return self.club_subscriptions.filter(
            status=ClubSubscription.Status.ACTIVE, ends_at__gt=timezone.now()
        ).exists()


class Lead(TimeStamped):
    """Заявка с сайта. Живёт в базе, а не только в Telegram."""

    class Status(models.TextChoices):
        NEW = 'new', 'Новая'
        CONTACTED = 'contacted', 'Связались'
        MEETING = 'meeting', 'Разбор назначен'
        PROPOSAL = 'proposal', 'Отправлено предложение'
        WON = 'won', 'Взяли в работу'
        LOST = 'lost', 'Отказ'

    class Source(models.TextChoices):
        FORM = 'form', 'Форма на сайте'
        SURVEY = 'survey', 'Разбор процессов'
        CLUB = 'club', 'Заявка в клуб'

    name = models.CharField('имя', max_length=120, blank=True)
    phone = models.CharField('телефон', max_length=32, db_index=True)
    area = models.CharField('сфера бизнеса', max_length=160, blank=True)
    telegram_username = models.CharField('telegram', max_length=64, blank=True)
    source = models.CharField('источник', max_length=16,
                              choices=Source.choices, default=Source.FORM)
    status = models.CharField('статус', max_length=16,
                              choices=Status.choices, default=Status.NEW, db_index=True)
    comment = models.TextField('комментарий', blank=True)
    delivered_to_telegram = models.BooleanField('ушла в Telegram', default=False)
    client = models.ForeignKey(Client, verbose_name='клиент', null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='leads')

    class Meta:
        verbose_name = 'заявка'
        verbose_name_plural = 'заявки'
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.name or "без имени"} · {format_phone(self.phone)}'

    def save(self, *args, **kwargs):
        self.phone = normalize_phone(self.phone)
        self.telegram_username = self.telegram_username.lstrip('@').strip()
        super().save(*args, **kwargs)

    @property
    def phone_pretty(self):
        return format_phone(self.phone)


class Project(TimeStamped):
    """Система, которую собираем конкретному клиенту."""

    class Status(models.TextChoices):
        DISCOVERY = 'discovery', 'Разбор процессов'
        PROPOSAL = 'proposal', 'Смета согласуется'
        BUILD = 'build', 'Сборка'
        SUPPORT = 'support', 'Запущен, месяц сопровождения'
        DONE = 'done', 'Завершён'
        FROZEN = 'frozen', 'Заморожен'

    client = models.ForeignKey(Client, verbose_name='клиент',
                               on_delete=models.CASCADE, related_name='projects')
    title = models.CharField('название', max_length=160)
    price = models.DecimalField('цена, ₽', max_digits=10, decimal_places=2, default=0)
    status = models.CharField('статус', max_length=16,
                              choices=Status.choices, default=Status.DISCOVERY)
    started_at = models.DateField('начали', null=True, blank=True)
    launched_at = models.DateField('запустили', null=True, blank=True)
    note = models.TextField('заметки', blank=True)

    class Meta:
        verbose_name = 'проект'
        verbose_name_plural = 'проекты'
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.title} — {self.client.name}'

    @property
    def paid_total(self):
        agg = self.payments.filter(status=Payment.Status.SUCCEEDED).aggregate(
            total=models.Sum('amount'))
        return agg['total'] or 0

    @property
    def debt(self):
        return self.price - self.paid_total


class Payment(TimeStamped):
    """Оплата. Заводится вручную (перевод, счёт) или приходит от эквайринга."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Ожидает оплаты'
        SUCCEEDED = 'succeeded', 'Оплачено'
        CANCELED = 'canceled', 'Отменена'

    class Purpose(models.TextChoices):
        PROJECT = 'project', 'Система под ключ'
        CLUB = 'club', 'Подписка на клуб'
        OTHER = 'other', 'Другое'

    client = models.ForeignKey(Client, verbose_name='клиент', null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='payments')
    project = models.ForeignKey(Project, verbose_name='проект', null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='payments')
    amount = models.DecimalField('сумма, ₽', max_digits=10, decimal_places=2)
    purpose = models.CharField('назначение', max_length=16,
                               choices=Purpose.choices, default=Purpose.PROJECT)
    status = models.CharField('статус', max_length=16,
                              choices=Status.choices, default=Status.PENDING, db_index=True)
    provider = models.CharField('способ', max_length=32, default='manual',
                                help_text='manual — перевод или счёт, yookassa — эквайринг')
    provider_payment_id = models.CharField('ID у провайдера', max_length=64,
                                           blank=True, db_index=True)
    payer_phone = models.CharField('телефон плательщика', max_length=32, blank=True)
    payer_telegram = models.CharField('telegram плательщика', max_length=64, blank=True)
    paid_at = models.DateTimeField('оплачено', null=True, blank=True)
    note = models.CharField('комментарий', max_length=200, blank=True)

    class Meta:
        verbose_name = 'оплата'
        verbose_name_plural = 'оплаты'
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.amount:.0f} ₽ · {self.get_purpose_display()} · {self.get_status_display()}'

    def mark_succeeded(self):
        """Отмечает оплату прошедшей. Повторный вызов ничего не меняет."""
        if self.status == self.Status.SUCCEEDED:
            return False
        self.status = self.Status.SUCCEEDED
        self.paid_at = timezone.now()
        self.save(update_fields=['status', 'paid_at', 'updated_at'])
        return True


class ClubSubscription(TimeStamped):
    """Доступ в закрытый Telegram-канал.

    Внутрь ведут два пути: купленная подписка или доступ, который мы отдаём
    клиенту, заказавшему систему. Второй — бесплатный, ставится вручную.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Ждёт оплаты'
        ACTIVE = 'active', 'Активна'
        EXPIRED = 'expired', 'Истекла'
        CANCELED = 'canceled', 'Отменена'

    class Plan(models.TextChoices):
        MONTH = 'month', 'Месяц'
        QUARTER = 'quarter', 'Три месяца'
        YEAR = 'year', 'Год'
        GIFT = 'gift', 'Доступ клиента (бесплатно)'

    PLAN_DAYS = {'month': 30, 'quarter': 92, 'year': 365, 'gift': 365}

    client = models.ForeignKey(Client, verbose_name='клиент', on_delete=models.CASCADE,
                               related_name='club_subscriptions')
    plan = models.CharField('тариф', max_length=16, choices=Plan.choices, default=Plan.MONTH)
    status = models.CharField('статус', max_length=16, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    price = models.DecimalField('цена, ₽', max_digits=10, decimal_places=2, default=0)
    starts_at = models.DateTimeField('начало', null=True, blank=True)
    ends_at = models.DateTimeField('окончание', null=True, blank=True, db_index=True)
    invite_link = models.URLField('ссылка-приглашение', blank=True, max_length=300)
    invite_sent_at = models.DateTimeField('приглашение выдано', null=True, blank=True)
    payment = models.OneToOneField(Payment, verbose_name='оплата', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='club_subscription')

    class Meta:
        verbose_name = 'подписка на клуб'
        verbose_name_plural = 'подписки на клуб'
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.client.name} · {self.get_plan_display()} · {self.get_status_display()}'

    @property
    def days(self):
        return self.PLAN_DAYS.get(self.plan, 30)

    def activate(self, from_moment=None):
        """Включает подписку. Если ещё не истекла — продлевает от её конца."""
        now = from_moment or timezone.now()
        base = self.ends_at if (self.ends_at and self.ends_at > now) else now
        self.starts_at = self.starts_at or now
        self.ends_at = base + timezone.timedelta(days=self.days)
        self.status = self.Status.ACTIVE
        self.save(update_fields=['starts_at', 'ends_at', 'status', 'updated_at'])
        return self

    @property
    def days_left(self):
        if not self.ends_at:
            return None
        return max(0, (self.ends_at - timezone.now()).days)


class Survey(TimeStamped):
    """Ответы на разбор процессов.

    Ответы лежат одним полем JSON, а не двадцатью столбцами: набор вопросов
    будет меняться, и каждая правка иначе тянула бы миграцию. Разбор по ним
    считается на лету в landing/survey.py — храним сырые ответы, чтобы
    пересчитать по новым правилам в любой момент.
    """

    name = models.CharField('имя', max_length=120, blank=True)
    phone = models.CharField('телефон', max_length=32, blank=True, db_index=True)
    telegram_username = models.CharField('telegram', max_length=64, blank=True)

    answers = models.JSONField('ответы', default=dict)

    allow_stories = models.BooleanField(
        'разрешил обезличенные примеры', default=False,
        help_text='Согласие использовать ответы в примерах без имени и контактов')

    lead = models.ForeignKey('Lead', verbose_name='заявка', null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='surveys')
    delivered_to_telegram = models.BooleanField('ушёл в Telegram', default=False)

    class Meta:
        verbose_name = 'разбор процессов'
        verbose_name_plural = 'разборы процессов'
        ordering = ('-created_at',)

    def __str__(self):
        who = self.name or 'без имени'
        return f'{who} · {self.created_at:%d.%m.%Y}' if self.created_at else who

    def save(self, *args, **kwargs):
        if self.phone:
            self.phone = normalize_phone(self.phone)
        self.telegram_username = self.telegram_username.lstrip('@').strip()
        super().save(*args, **kwargs)

    @property
    def phone_pretty(self):
        return format_phone(self.phone) if self.phone else ''

    @property
    def area(self):
        """Сфера словами — она нужна и в списке, и в уведомлении."""
        from .survey import _label
        value = self.answers.get('area')
        other = self.answers.get('area_other')
        return other or (_label('area', value) if value else '')

    def diagnose(self):
        from .survey import diagnose
        return diagnose(self.answers)

    def readable(self):
        from .survey import readable
        return readable(self.answers)
