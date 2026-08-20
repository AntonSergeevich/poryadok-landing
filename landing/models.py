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

    # Учётная запись для входа в кабинет. Её нет, пока доступ не выдан:
    # карточку клиента заводят сразу, а кабинет — когда есть что показывать.
    user = models.OneToOneField('auth.User', verbose_name='вход в кабинет',
                                null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='client_card')

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
        BUILD = 'build', 'Собрал в конструкторе'

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

    @property
    def ordered_stages(self):
        """Этапы по порядку. Отдельным свойством — чтобы в шаблоне
        не повторять сортировку и не получить два разных порядка
        на шкале и в списке."""
        return list(self.stages.all())

    @property
    def current_stage(self):
        """Этап, который идёт сейчас.

        Сначала ищем начатый; если такого нет — первый незакрытый;
        если закрыты все — последний. Пустой ответ здесь недопустим:
        шкале нужно что-то подсветить в любом состоянии проекта.
        """
        stages = self.ordered_stages
        if not stages:
            return None
        running = [s for s in stages if s.status in
                   (Stage.Status.RUNNING, Stage.Status.REVIEW)]
        if running:
            return running[0]
        waiting = [s for s in stages if not s.is_done]
        return waiting[0] if waiting else stages[-1]

    @property
    def progress(self):
        """Сколько процентов пути пройдено — по дням плана, а не по числу
        этапов.

        «Разбор» и «запуск» стоят на шкале одинаковыми точками, хотя первый
        занимает три дня, а второй — месяц. Отсюда и берётся ощущение
        «мы застряли»: человек видит восемь равных шагов и считает, что
        после третьего должно пройти три восьмых срока.
        """
        stages = self.ordered_stages
        total = sum(s.planned_days for s in stages)
        if not total:
            return 0
        done = sum(s.planned_days for s in stages if s.is_done)
        return round(done * 100 / total)

    def client_todo(self):
        """Задачи, которые ждут заказчика. Это первое, что он должен
        увидеть в кабинете, — и единственное, ради чего его туда зовут."""
        rows = []
        for stage in self.ordered_stages:
            if stage.is_done:
                continue
            rows.extend(stage.open_tasks(who=StageTask.Who.CLIENT))
        return rows

    def build_stages(self):
        """Разложить этапы по плану. Повторный вызов ничего не портит:
        если этапы уже есть, значит их могли поправить руками."""
        if self.stages.exists():
            return 0
        Stage.objects.bulk_create([
            Stage(project=self, number=number, title=title,
                  summary=summary, planned_days=days)
            for number, title, summary, days in STAGE_PLAN
        ])
        return len(STAGE_PLAN)


# ── Этапы и задачи ───────────────────────────────────────────────────
# Заказчик заходит в кабинет с одним вопросом: «что происходит и ждут ли
# чего-то от меня». Это два разных вопроса, и отвечают на них две разные
# сущности: этап говорит «где мы», задача — «чего ждём».
#
# Смешивать их в одну таблицу нельзя. Список задач без этапов — лента,
# в которой ничего никогда не заканчивается. Этапы без задач — красивая
# шкала, по которой непонятно, чей сейчас ход.

STAGE_PLAN = [
    (1, 'Разбор', 'Смотрим, как всё устроено сейчас: откуда приходят '
                  'заявки, где они теряются, что считается руками.', 3),
    (2, 'Смета', 'Собираем состав системы и цену. Пока не согласовали — '
                 'ничего не начинаем.', 2),
    (3, 'Каркас', 'Заводим данные: клиенты, услуги, цены. То, на чём '
                  'потом стоит всё остальное.', 5),
    (4, 'Запись и заявки', 'Форма, расписание, напоминания. С этого места '
                           'заявка перестаёт теряться.', 7),
    (5, 'Деньги', 'Оплаты, долги, отчёт за месяц. Сколько заработали — '
                  'ответ считается сам.', 5),
    (6, 'Кабинеты', 'Ваш рабочий стол и то, что видит клиент.', 6),
    (7, 'Перенос', 'Переносим то, что уже есть: базу клиентов, историю, '
                   'остатки по абонементам.', 4),
    (8, 'Запуск и месяц рядом', 'Ставим на боевой сервер, показываю, как '
                                'этим пользоваться, месяц правлю по ходу.', 20),
]


class Stage(TimeStamped):
    """Этап проекта — «где мы сейчас».

    Этапы не спрашиваются при заведении проекта: список известен заранее
    и одинаков. Форма на восемь строк — верный способ завести проект
    вообще без этапов.
    """

    class Status(models.TextChoices):
        WAITING = 'waiting', 'Не начат'
        RUNNING = 'running', 'В работе'
        REVIEW = 'review', 'На согласовании'
        DONE = 'done', 'Готово'

    class Waiting(models.TextChoices):
        NOBODY = '', '—'
        ME = 'me', 'За мной'
        CLIENT = 'client', 'Жду вас'

    project = models.ForeignKey(Project, verbose_name='проект',
                                on_delete=models.CASCADE, related_name='stages')
    number = models.PositiveSmallIntegerField('номер')
    title = models.CharField('этап', max_length=120)
    summary = models.TextField('что здесь происходит', blank=True)
    status = models.CharField('статус', max_length=12,
                              choices=Status.choices, default=Status.WAITING)
    # Разделяет «я не успел» и «жду ответа заказчика». Без этого поля
    # обе стороны считают, что ход за другой, и проект стоит молча.
    waiting_on = models.CharField('кого ждём', max_length=8, blank=True,
                                  choices=Waiting.choices, default=Waiting.NOBODY)
    planned_days = models.PositiveSmallIntegerField('план, рабочих дней', default=5)
    started_at = models.DateField('начат', null=True, blank=True)
    finished_at = models.DateField('завершён', null=True, blank=True)

    class Meta:
        verbose_name = 'этап'
        verbose_name_plural = 'этапы'
        ordering = ('project', 'number')
        constraints = [
            models.UniqueConstraint(fields=['project', 'number'],
                                    name='uniq_stage_number'),
        ]

    def __str__(self):
        return f'{self.number}. {self.title}'

    @property
    def is_done(self):
        return self.status == self.Status.DONE

    @property
    def waits_client(self):
        return self.waiting_on == self.Waiting.CLIENT

    def open_tasks(self, who=None):
        """Незакрытые задачи этапа, при желании — только чьи-то.

        Считаем по уже загруженному списку, а не запросом: карточка этапа
        рисуется восемь раз подряд, и восемь лишних запросов на страницу
        появляются незаметно, а потом остаются навсегда.
        """
        rows = [task for task in self.tasks.all() if not task.is_done]
        if who is None:
            return rows
        return [task for task in rows
                if task.who in (who, StageTask.Who.BOTH)]

    def mark(self, status):
        """Сменить статус, проставив даты по смыслу.

        Даты руками не заводятся: их забывают, а потом по проекту
        невозможно сказать, сколько он на самом деле шёл.
        """
        today = timezone.localdate()
        self.status = status
        if status == self.Status.DONE:
            self.finished_at = self.finished_at or today
            self.started_at = self.started_at or today
            self.waiting_on = self.Waiting.NOBODY
        else:
            self.finished_at = None
            if status != self.Status.WAITING:
                self.started_at = self.started_at or today
            else:
                self.started_at = None
        self.save(update_fields=['status', 'started_at', 'finished_at',
                                 'waiting_on', 'updated_at'])


class StageTask(TimeStamped):
    """Что конкретно должно быть сделано на этапе прямо сейчас.

    У задачи всегда есть исполнитель, и заказчик его видит. Половина
    тревожных сообщений — это «я не понял, ждут ли чего-то от меня»,
    и цветная метка отвечает на это без переписки.
    """

    class Who(models.TextChoices):
        ME = 'me', 'За мной'
        CLIENT = 'client', 'За вами'
        BOTH = 'both', 'Вместе'

    stage = models.ForeignKey(Stage, verbose_name='этап',
                              on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField('что сделать', max_length=250)
    comment = models.TextField('пояснение', blank=True)
    who = models.CharField('кто делает', max_length=8,
                           choices=Who.choices, default=Who.ME)
    is_done = models.BooleanField('сделано', default=False)
    done_at = models.DateTimeField('когда сделано', null=True, blank=True)
    order = models.PositiveSmallIntegerField('порядок', default=100)

    class Meta:
        verbose_name = 'задача этапа'
        verbose_name_plural = 'задачи этапа'
        # Сделанные уходят вниз: наверху всегда то, что ещё предстоит.
        ordering = ('is_done', 'order', 'pk')

    def __str__(self):
        return self.title

    @property
    def is_client(self):
        return self.who in (self.Who.CLIENT, self.Who.BOTH)

    def toggle(self, done):
        self.is_done = bool(done)
        self.done_at = timezone.now() if self.is_done else None
        self.save(update_fields=['is_done', 'done_at', 'updated_at'])

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
    reminded_at = models.DateTimeField('напоминание отправлено', null=True, blank=True,
                                       help_text='Чтобы не напомнить дважды за один срок')
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
