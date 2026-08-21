"""Модели CRM: заявки, клиенты, проекты, оплаты, подписка на закрытый клуб.

Смысл этих моделей — чтобы ни одна заявка и ни одна оплата не жили
в переписке и в голове. Ровно то, что мы продаём клиентам.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
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

    # Реквизиты для договора. Лежат у клиента, а не у договора: они
    # принадлежат ему и не меняются от документа к документу. В сам
    # договор они уходят снимком — если через год заказчик переедет,
    # подписанный документ обязан остаться таким, каким его подписали.
    legal_name = models.CharField(
        'наименование для договора', max_length=250, blank=True,
        help_text='ИП Иванов Иван Иванович · ООО «Ромашка» · '
                  'Иванов Иван Иванович')
    inn = models.CharField('ИНН', max_length=12, blank=True)
    address = models.CharField('адрес', max_length=250, blank=True)
    signer = models.CharField(
        'кто подписывает', max_length=160, blank=True,
        help_text='«директора Иванова И. И.» — в родительном падеже, '
                  'как это стоит в договоре после слов «в лице».')

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
        # «Думает» — не то же самое, что «связались». Это состояние,
        # из которого заявка сама не выйдет: если о ней не напомнить,
        # она тихо умрёт через месяц, и никто не заметит.
        THINKING = 'thinking', 'Думает'
        WON = 'won', 'Взяли в работу'
        LOST = 'lost', 'Отказ'

    # Сколько дней ждать, когда человек взял паузу. Три — потому что
    # через неделю разговор уже надо начинать заново.
    THINKING_DAYS = 3

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

    # Заметки после разговора — отдельно от comment, где лежит то, что
    # человек написал сам. Смешивать их нельзя: одно принадлежит ему,
    # другое мне, и переписывать чужие слова своими выводами нечестно.
    note = models.TextField('заметки после разговора', blank=True)

    # Когда напомнить. Заполняется само при статусе «думает», но правится
    # руками: договорились созвониться в понедельник — значит в понедельник.
    remind_at = models.DateTimeField('напомнить', null=True, blank=True,
                                     db_index=True)
    reminded_at = models.DateTimeField('напоминание отправлено',
                                       null=True, blank=True)

    # Почему отказались. Одно поле, которое через год объяснит, что
    # чинить в предложении, — если его заполнять.
    lost_reason = models.CharField('причина отказа', max_length=250, blank=True)

    # Состав, собранный в конструкторе. Хранится списком кодов блоков,
    # а не текстом: текст годится для чтения, но по нему нельзя
    # пересчитать цену, когда цены изменятся, и нельзя перенести набор
    # в проект. Пришедшее с сайта попадает сюда же — разговор начинается
    # не с нуля, а с того, что человек собрал сам.
    build_blocks = models.JSONField('состав системы', default=list, blank=True)
    build_scale = models.CharField('масштаб', max_length=16, blank=True)

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

    @property
    def is_open(self):
        """Заявка ещё в работе. Закрытые — взятые и отказавшиеся."""
        return self.status not in (self.Status.WON, self.Status.LOST)

    @property
    def is_overdue(self):
        """Срок напоминания прошёл, а напоминания не было."""
        return bool(self.remind_at and not self.reminded_at
                    and self.remind_at <= timezone.now())

    def set_status(self, status, reason='', remind_days=None):
        """Сменить статус, проставив всё, что из него следует.

        Отдельным методом, потому что у статуса есть последствия, и
        забыть их — обычное дело. «Думает» без даты напоминания — это
        просто заявка, о которой забудут; «отказ» без причины —
        потерянный урок.
        """
        self.status = status
        fields = ['status', 'updated_at']

        if status == self.Status.THINKING:
            days = self.THINKING_DAYS if remind_days is None else remind_days
            self.remind_at = timezone.now() + timedelta(days=days)
            # Новый срок — новое напоминание. Иначе отметка о прошлом
            # заглушит следующее, и заявка снова потеряется.
            self.reminded_at = None
            fields += ['remind_at', 'reminded_at']
        elif status in (self.Status.WON, self.Status.LOST):
            # Закрытую заявку напоминать незачем.
            self.remind_at = None
            fields.append('remind_at')

        if status == self.Status.LOST:
            self.lost_reason = (reason or '')[:250]
            fields.append('lost_reason')

        self.save(update_fields=fields)


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

    # Состав системы. Приезжает из заявки и правится в кабинете тем же
    # конструктором, что и на сайте. Отсюда же берётся Приложение № 1
    # к договору: состав, названный на словах, и состав, за который
    # подписались, обязаны быть одним списком.
    build_blocks = models.JSONField('состав системы', default=list, blank=True)
    build_scale = models.CharField('масштаб', max_length=16, blank=True)

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

    @property
    def contract(self):
        """Договор по проекту — последний неотменённый.

        Их может быть несколько: отменённый черновик, потом настоящий.
        Показывать надо один, и это всегда свежий.
        """
        return self.contracts.exclude(
            status=Contract.Status.CANCELED).first()

    @property
    def planned_days(self):
        """Срок по календарному плану. Он же уходит в договор — чтобы
        срок в документе и срок на шкале были одним числом, а не двумя
        независимыми обещаниями."""
        return sum(s.planned_days for s in self.ordered_stages)

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

# ── Переписка по проекту ─────────────────────────────────────────────
# Она здесь не для удобства — для доказательной базы. В мессенджере
# «я вам присылал» не значит ничего: сообщение удаляется, чат чистится,
# файл теряется вместе с ним.
#
# Отсюда три правила, которые не обсуждаются:
#   • сообщения не удаляются и не правятся;
#   • имя автора копируется в момент отправки — учётную запись можно
#     переименовать, а переписка обязана остаться читаемой;
#   • файлы хранятся вместе с сообщением и его временем.

class Message(TimeStamped):
    """Сообщение в переписке по проекту."""

    project = models.ForeignKey(Project, verbose_name='проект',
                                on_delete=models.CASCADE, related_name='messages')
    # Автора можно удалить из системы, сообщение — нет. Поэтому связь
    # обнуляемая, а имя лежит рядом отдельной копией.
    author = models.ForeignKey('auth.User', verbose_name='автор', null=True,
                               blank=True, on_delete=models.SET_NULL,
                               related_name='messages')
    author_name = models.CharField('имя автора', max_length=120)
    author_is_owner = models.BooleanField('от исполнителя', default=False)
    text = models.TextField('текст', blank=True)
    edited_at = models.DateTimeField('поправлено', null=True, blank=True)
    read_at = models.DateTimeField('прочитано другой стороной',
                                   null=True, blank=True)

    class Meta:
        verbose_name = 'сообщение'
        verbose_name_plural = 'переписка'
        ordering = ('created_at', 'pk')

    def __str__(self):
        return f'{self.author_name}: {self.text[:40]}'

    # Минуту на исправление опечатки — и всё. Это закрывает единственный
    # честный случай («отправил не ту цифру») и не даёт переписать
    # историю: переписка нужна как доказательная база, а переписанная
    # задним числом не доказывает ничего.
    EDIT_WINDOW = timedelta(minutes=1)

    @property
    def is_read(self):
        return self.read_at is not None

    @property
    def edit_until(self):
        return self.created_at + self.EDIT_WINDOW

    @property
    def can_edit(self):
        return timezone.now() < self.edit_until

    @property
    def edit_left(self):
        """Сколько секунд осталось на правку. Ноль — время вышло.

        Считается на сервере: браузер может стоять с открытой вкладкой
        час, и «минута» по его часам к делу отношения не имеет.
        """
        return max(int((self.edit_until - timezone.now()).total_seconds()), 0)


def chat_upload_to(instance, filename):
    """Файлы разложены по месяцам: иначе одна папка на все проекты
    однажды перестаёт открываться."""
    return f'chat/{timezone.now():%Y/%m}/{filename}'


class MessageFile(TimeStamped):
    """Файл, приложенный к сообщению.

    Живёт вместе с сообщением, а не отдельным разделом «файлы проекта»:
    файл почти всегда объясняется словами рядом, и разлучать их значит
    заставить искать в двух местах.
    """

    IMAGE_SUFFIXES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic')

    message = models.ForeignKey(Message, verbose_name='сообщение',
                                on_delete=models.CASCADE, related_name='files')
    file = models.FileField('файл', upload_to=chat_upload_to)
    name = models.CharField('имя файла', max_length=250)
    size = models.PositiveIntegerField('размер, байт', default=0)

    class Meta:
        verbose_name = 'файл в переписке'
        verbose_name_plural = 'файлы в переписке'
        ordering = ('pk',)

    def __str__(self):
        return self.name

    @property
    def is_image(self):
        return self.name.lower().endswith(self.IMAGE_SUFFIXES)

    @property
    def human_size(self):
        """«2,4 МБ» вместо «2517948». Второе не читает никто."""
        size = self.size or 0
        if size < 1024:
            return f'{size} Б'
        if size < 1024 * 1024:
            return f'{size / 1024:.0f} КБ'
        return f'{size / (1024 * 1024):.1f} МБ'.replace('.', ',')

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


# ── Договор ──────────────────────────────────────────────────────────
#
# Договор — единственная сущность здесь, которую нельзя менять задним
# числом. Всё остальное в кабинете правится: заявка, этап, цена, даже
# сообщение в первую минуту. Договор — нет: его распечатали, подписали
# и положили в папку, и через год стороны обязаны увидеть один и тот же
# текст.
#
# Отсюда снимок. При выставлении на подписание готовый текст, реквизиты
# и суммы складываются в `body` и `data` и больше не пересобираются.
# Правка landing/contract.py меняет только будущие договоры — ни одного
# уже выставленного она не трогает.


def contract_upload_to(instance, filename):
    """Подписанные договоры — по годам. Их немного, но искать их будут
    руками, и «все файлы в одной папке» здесь особенно неудобно."""
    return f'contracts/{timezone.now():%Y}/{filename}'


class Contract(TimeStamped):
    """Договор оказания услуг по проекту."""

    class Status(models.TextChoices):
        # Черновик виден только мне. Он нужен затем, что договор
        # перечитывают перед отправкой — а не после.
        DRAFT = 'draft', 'Черновик'
        ISSUED = 'issued', 'Выставлен на подписание'
        SIGNED = 'signed', 'Подписан'
        CANCELED = 'canceled', 'Отменён'

    project = models.ForeignKey(Project, verbose_name='проект',
                                on_delete=models.CASCADE,
                                related_name='contracts')
    number = models.CharField('номер', max_length=32)
    date = models.DateField('дата')
    status = models.CharField('статус', max_length=16,
                              choices=Status.choices, default=Status.DRAFT,
                              db_index=True)

    system_name = models.CharField('название системы', max_length=200)
    amount = models.DecimalField('сумма, ₽', max_digits=10, decimal_places=2,
                                 default=0)
    prepay_percent = models.PositiveSmallIntegerField('аванс, %', default=50)
    term_days = models.PositiveSmallIntegerField('срок, рабочих дней',
                                                 default=0)
    warranty_days = models.PositiveSmallIntegerField('гарантия, дней',
                                                     default=30)
    support_days = models.PositiveSmallIntegerField('сопровождение, дней',
                                                    default=30)
    scope = models.TextField('состав системы', blank=True,
                             help_text='Приложение № 1. По строке на блок.')

    # Снимок. Текст разделов и все подставленные значения — как они
    # выглядели в момент выставления.
    body = models.JSONField('текст договора', default=list, blank=True)
    data = models.JSONField('подставленные данные', default=dict, blank=True)

    issued_at = models.DateTimeField('выставлен', null=True, blank=True)
    signed_at = models.DateTimeField('подписан', null=True, blank=True)
    # Скан подписанного. Загружает заказчик из своего кабинета — это
    # и есть подпись с точки зрения п. 10.1 договора.
    signed_file = models.FileField('подписанный скан', blank=True,
                                   upload_to=contract_upload_to)
    signed_name = models.CharField('имя файла', max_length=250, blank=True)

    class Meta:
        verbose_name = 'договор'
        verbose_name_plural = 'договоры'
        ordering = ('-created_at',)

    def __str__(self):
        return f'Договор № {self.number} от {self.date:%d.%m.%Y}'

    @property
    def prepay(self):
        """Аванс в рублях. Считается от процента, а не хранится отдельно:
        два поля с одной суммой однажды разойдутся, и спорить придётся
        уже по подписанному документу."""
        return (self.amount * self.prepay_percent / 100).quantize(
            Decimal('0.01'))

    @property
    def rest(self):
        return self.amount - self.prepay

    @property
    def is_visible_to_client(self):
        """Черновик заказчику не показывается. Он для того и черновик."""
        return self.status in (self.Status.ISSUED, self.Status.SIGNED)

    @property
    def waiting_signature(self):
        return self.status == self.Status.ISSUED

    @classmethod
    def next_number(cls, when=None):
        """Следующий номер в году: 03-2026.

        Сквозная нумерация внутри года — то, как это заведено в бумаге,
        и то, что спрашивает бухгалтерия заказчика. Номер по номеру
        строки в базе («договор № 47») выглядит странно у исполнителя,
        у которого их было четыре.
        """
        when = when or timezone.localdate()
        used = cls.objects.filter(date__year=when.year).count()
        return f'{used + 1:02d}-{when.year}'


# ── Файлы к заявке и проекту ─────────────────────────────────────────
#
# Переписка уже умеет файлы, и на первый взгляд этого достаточно. Но
# в переписке файл живёт вместе со словами и вместе с ними уезжает вверх:
# «макет, который прислали три недели назад» ищут прокруткой, и находят
# не всегда.
#
# Здесь другое: полка. Примеры дизайна, замеры, выгрузка старой базы,
# фотографии помещения — то, к чему возвращаются по многу раз и что
# не должно зависеть от того, в каком сообщении его прислали.
#
# И главное: у заявки переписки нет вовсе. Файл, присланный до того, как
# завёлся проект, до сих пор было некуда положить.


def attachment_upload_to(instance, filename):
    """По месяцам — как и файлы переписки. Одна папка на все проекты
    перестаёт открываться раньше, чем кажется."""
    return f'files/{timezone.now():%Y/%m}/{filename}'


class Attachment(TimeStamped):
    """Документ или фотография, приложенные к заявке или проекту."""

    IMAGE_SUFFIXES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic',
                      '.avif', '.svg')

    # Обе связи необязательные, но пустыми обе быть не должны: файл,
    # не привязанный ни к чему, не показывается нигде и остаётся занимать
    # место. Проверка в clean(), а не в базе: правило это прикладное,
    # и объяснить его человеку должна форма, а не сообщение о нарушении
    # ограничения целостности.
    lead = models.ForeignKey('Lead', verbose_name='заявка', null=True,
                             blank=True, on_delete=models.CASCADE,
                             related_name='files')
    project = models.ForeignKey('Project', verbose_name='проект', null=True,
                                blank=True, on_delete=models.CASCADE,
                                related_name='files')

    file = models.FileField('файл', upload_to=attachment_upload_to)
    name = models.CharField('имя файла', max_length=250)
    size = models.PositiveIntegerField('размер, байт', default=0)
    note = models.CharField('что это', max_length=250, blank=True)

    # Кто принёс. Заказчик присылает примеры дизайна сам, и в списке
    # это должно быть видно: «прислали» и «положил сам» — разные вещи.
    from_client = models.BooleanField('от заказчика', default=False)
    author_name = models.CharField('кто добавил', max_length=120, blank=True)

    class Meta:
        verbose_name = 'файл'
        verbose_name_plural = 'файлы'
        ordering = ('-created_at', '-pk')

    def __str__(self):
        return self.name

    @property
    def is_image(self):
        return self.name.lower().endswith(self.IMAGE_SUFFIXES)

    @property
    def human_size(self):
        """«2,4 МБ» вместо «2517948». Второе не читает никто."""
        size = self.size or 0
        if size < 1024:
            return f'{size} Б'
        if size < 1024 * 1024:
            return f'{size / 1024:.0f} КБ'
        return f'{size / (1024 * 1024):.1f} МБ'.replace('.', ',')


# ── Портфолио ────────────────────────────────────────────────────────
#
# Работы жили списком в landing/works.py — двумя словарями в коде.
# Пока работ две, это честнее базы: править их приходилось раз в полгода,
# а код виден в истории изменений целиком.
#
# Дальше так нельзя. Заказчиков будет больше, и добавление работы
# не должно означать правку файла, выкладку и перезапуск: так работа
# не добавляется никогда — она откладывается «до следующего раза, когда
# буду в коде». Поэтому работы переезжают в базу и заводятся из кабинета,
# рядом с проектом, из которого выросли.
#
# Связь с проектом необязательная. Первые две работы сделаны до того,
# как появились проекты в кабинете, и требовать связь значило бы
# придумывать им задним числом проекты, которых не было.


def work_upload_to(instance, filename):
    """Снимки работ — по адресу работы. Файлов на работу четыре-шесть,
    и в общей папке они перемешиваются уже на третьей работе."""
    slug = getattr(instance, 'work', None) and instance.work.slug or 'raboty'
    return f'works/{slug}/{filename}'


class Work(TimeStamped):
    """Работа в портфолио: что было у заказчика до системы и что стало.

    Поля `was_text` и `now_text` хранятся текстом по строке на пункт,
    а наружу отдаются списками (`was`, `now`). Причина в том, что вводят
    их в одно поле — списком строк, — а отдельная таблица на каждый пункт
    превратила бы добавление работы в двадцать нажатий «добавить строку».
    """

    project = models.ForeignKey(
        Project, verbose_name='проект', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='works',
        help_text='Необязательно. Связь нужна, чтобы видеть, из какой '
                  'работы выросла эта страница.')

    slug = models.SlugField('адрес', max_length=60, unique=True,
                            help_text='Латиницей: адрес страницы работы.')
    title = models.CharField('как обращаться', max_length=120)
    role = models.CharField('чем занимается', max_length=160)
    site = models.CharField('сайт', max_length=160, blank=True,
                            help_text='Без https:// — оно добавится само.')
    city = models.CharField('город', max_length=120, blank=True)

    # Срок и «когда открылся» — две разные вещи, и путать их нельзя:
    # «две недели» звучит долго, пока не сказано, что работать система
    # начала на третий день, а остальное время её доводили под человека.
    term = models.CharField('срок', max_length=60, blank=True)
    term_note = models.CharField('что было со сроком', max_length=250,
                                 blank=True)

    lede = models.TextField('одна строка о деле', blank=True)
    was_text = models.TextField(
        'было', blank=True,
        help_text='По строке на пункт. Словами заказчика, а не «оптимизация '
                  'бизнес-процессов»: читатель узнаёт себя именно в них.')
    now_text = models.TextField('стало', blank=True,
                                help_text='По строке на пункт.')

    order = models.PositiveSmallIntegerField('порядок', default=100)
    # По умолчанию не показывается. Работа заводится пустой — без «было»,
    # «стало» и снимков, — и опубликованная в этот момент она обещает
    # читателю страницу, за которой ничего нет.
    is_published = models.BooleanField(
        'показывать на сайте', default=False,
        help_text='Снятая с публикации работа остаётся в кабинете, '
                  'но исчезает с сайта.')

    class Meta:
        verbose_name = 'работа в портфолио'
        verbose_name_plural = 'портфолио'
        ordering = ('order', '-created_at')

    def __str__(self):
        return f'{self.title} · {self.role}'

    @staticmethod
    def _lines(text):
        return [line.strip() for line in (text or '').splitlines() if line.strip()]

    @property
    def was(self):
        return self._lines(self.was_text)

    @property
    def now(self):
        return self._lines(self.now_text)

    @property
    def facts(self):
        """Пары «подпись — число» для шаблона.

        Отдаются парами, а не объектами, потому что шаблон разбирает их
        как `{% for label, value in work.facts %}` — ровно так же, как
        когда работы лежали списком в коде. Разметку из-за переезда
        в базу переписывать не пришлось.
        """
        return [(fact.label, fact.value) for fact in self.fact_rows.all()]

    @property
    def shots(self):
        return list(self.shot_rows.all())

    @property
    def cover(self):
        """Снимок для плитки на главной. Первый — потому что первым ставят
        главный, а не потому, что так вышло."""
        return self.shot_rows.first()


class WorkFact(TimeStamped):
    """Проверяемое число рядом с работой.

    «Автотестов — 203» работает не потому, что число большое, а потому,
    что его можно проверить. Поэтому здесь текст, а не число: «две недели»
    и «19» одинаково уместны.
    """

    work = models.ForeignKey(Work, verbose_name='работа',
                             on_delete=models.CASCADE, related_name='fact_rows')
    label = models.CharField('подпись', max_length=120)
    value = models.CharField('значение', max_length=60)
    order = models.PositiveSmallIntegerField('порядок', default=10)

    class Meta:
        verbose_name = 'число в работе'
        verbose_name_plural = 'числа в работе'
        ordering = ('order', 'pk')

    def __str__(self):
        return f'{self.label}: {self.value}'


class WorkShot(TimeStamped):
    """Снимок экрана.

    Источников два, и это не небрежность. Снимки первых работ лежат
    в статике: они сняты руками до того, как появился этот раздел,
    и там же и должны остаться. Новые загружаются из кабинета и попадают
    в media.

    Копировать старые в media отдельной миграцией было бы красивее
    в описании и хуже на деле: миграция, которая трогает файлы, ломается
    там, где её труднее всего чинить, — на боевом сервере, где у папки
    другой владелец.
    """

    work = models.ForeignKey(Work, verbose_name='работа',
                             on_delete=models.CASCADE, related_name='shot_rows')
    caption = models.CharField('подпись', max_length=250, blank=True)
    order = models.PositiveSmallIntegerField('порядок', default=10)

    image = models.ImageField('снимок', blank=True, upload_to=work_upload_to)
    static_name = models.CharField(
        'имя файла в статике', max_length=120, blank=True,
        help_text='Для снимков, лежащих в landing/static/landing/img/cases/. '
                  'Без расширения: рядом должны быть имя.webp и имя-sm.webp.')

    class Meta:
        verbose_name = 'снимок работы'
        verbose_name_plural = 'снимки работы'
        ordering = ('order', 'pk')

    def __str__(self):
        return self.caption or self.static_name or str(self.image)

    @property
    def src(self):
        """Полный снимок."""
        if self.image:
            return self.image.url
        if self.static_name:
            return f'{settings.STATIC_URL}landing/img/cases/{self.static_name}.webp'
        return ''

    @property
    def thumb(self):
        """Плитка. У загруженных снимков уменьшенной копии нет — показываем
        тот же файл: заводить пережатие ради четырёх картинок на работу
        значит завести ещё и очередь, и место, где она однажды встанет."""
        if self.image:
            return self.image.url
        if self.static_name:
            return f'{settings.STATIC_URL}landing/img/cases/{self.static_name}-sm.webp'
        return ''


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
