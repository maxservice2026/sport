import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Sport(models.Model):
    name = models.CharField(max_length=50, unique=True, verbose_name='Název')

    class Meta:
        verbose_name = 'Sport'
        verbose_name_plural = 'Sporty'

    def __str__(self):
        return self.name


class Group(models.Model):
    REG_ENABLED = 'enabled'
    REG_DISABLED = 'disabled'
    REG_FULL = 'full'
    REGISTRATION_CHOICES = [
        (REG_ENABLED, 'Zapnuto'),
        (REG_DISABLED, 'Vypnuto'),
        (REG_FULL, 'Obsazeno'),
    ]

    sport = models.ForeignKey(Sport, on_delete=models.CASCADE, related_name='groups')
    name = models.CharField(max_length=100, verbose_name='Název')
    training_days = models.JSONField(default=list, blank=True, verbose_name='Dny tréninků')
    start_date = models.DateField(null=True, blank=True, verbose_name='Začátek skupiny')
    end_date = models.DateField(null=True, blank=True, verbose_name='Konec skupiny')
    registration_state = models.CharField(
        max_length=20,
        choices=REGISTRATION_CHOICES,
        default=REG_ENABLED,
        verbose_name='Stav registrace',
    )
    max_members = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Maximální počet členů',
    )
    allow_combined_registration = models.BooleanField(
        default=True,
        verbose_name='Povolit kombinaci s jinou skupinou',
    )
    trainers = models.ManyToManyField(settings.AUTH_USER_MODEL, through='TrainerGroup', related_name='assigned_groups')

    class Meta:
        unique_together = ('sport', 'name')
        verbose_name = 'Skupina'
        verbose_name_plural = 'Skupiny'

    def __str__(self):
        return f"{self.sport.name} - {self.name}"

    @property
    def active_members_count(self):
        return self.memberships.filter(active=True).count()

    @property
    def free_slots(self):
        if not self.max_members:
            return None
        return max(0, self.max_members - self.active_members_count)


class AttendanceOption(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='attendance_options')
    name = models.CharField(max_length=100, verbose_name='Název')
    frequency_per_week = models.PositiveSmallIntegerField(verbose_name='Frekvence / týden')
    price_czk = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Cena (Kč)')

    class Meta:
        unique_together = ('group', 'name')
        verbose_name = 'Docházková varianta'
        verbose_name_plural = 'Docházkové varianty'

    def __str__(self):
        return f"{self.group.name} - {self.name}"


class Child(models.Model):
    parent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='children')
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='Unikátní ID')
    created_at = models.DateTimeField(default=timezone.now, editable=False, verbose_name='Vytvořeno')
    variable_symbol = models.CharField(max_length=4, unique=True, blank=True, verbose_name='Variabilní symbol')
    first_name = models.CharField(max_length=60, verbose_name='Jméno')
    last_name = models.CharField(max_length=60, verbose_name='Příjmení')
    birth_number = models.CharField(max_length=11, blank=True, null=True, unique=True, verbose_name='Rodné číslo')
    passport_number = models.CharField(max_length=30, blank=True, null=True, unique=True, verbose_name='Číslo pasu')
    phone = models.CharField(max_length=30, blank=True, verbose_name='Telefon')

    class Meta:
        verbose_name = 'Dítě'
        verbose_name_plural = 'Děti'

    def clean(self):
        if not self.birth_number and not self.passport_number:
            raise ValidationError('Je potřeba vyplnit rodné číslo nebo číslo pasu.')

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.variable_symbol:
            if self.pk > 9999:
                raise ValidationError('Byl dosažen limit 4 číslic pro variabilní symbol.')
            self.variable_symbol = str(self.pk)
            super().save(update_fields=['variable_symbol'])

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Membership(models.Model):
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='memberships')
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='memberships')
    attendance_option = models.ForeignKey(AttendanceOption, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Docházková varianta')
    registered_at = models.DateTimeField(default=timezone.now, verbose_name='Datum registrace')
    billing_start_month = models.DateField(null=True, blank=True, verbose_name='Zařazení od měsíce')
    active = models.BooleanField(default=True, verbose_name='Aktivní')

    class Meta:
        unique_together = ('child', 'group')
        verbose_name = 'Členství'
        verbose_name_plural = 'Členství'

    def __str__(self):
        return f"{self.child} - {self.group}"


class TrainerGroup(models.Model):
    trainer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, limit_choices_to={'role': 'trainer'})
    group = models.ForeignKey(Group, on_delete=models.CASCADE)

    class Meta:
        unique_together = ('trainer', 'group')
        verbose_name = 'Trenérská skupina'
        verbose_name_plural = 'Trenérské skupiny'

    def __str__(self):
        return f"{self.trainer} -> {self.group}"


class ChildArchiveLog(models.Model):
    EVENT_INFO = 'info'
    EVENT_REGISTRATION = 'registration'
    EVENT_MEMBERSHIP = 'membership'
    EVENT_ATTENDANCE = 'attendance'
    EVENT_PROFILE = 'profile'
    EVENT_CHOICES = [
        (EVENT_INFO, 'Info'),
        (EVENT_REGISTRATION, 'Registrace'),
        (EVENT_MEMBERSHIP, 'Skupina'),
        (EVENT_ATTENDANCE, 'Docházka'),
        (EVENT_PROFILE, 'Profil'),
    ]

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='archive_logs', verbose_name='Dítě')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='child_archive_events',
        verbose_name='Uživatel',
    )
    event_type = models.CharField(max_length=20, choices=EVENT_CHOICES, default=EVENT_INFO, verbose_name='Typ události')
    message = models.TextField(verbose_name='Popis')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Vytvořeno')

    class Meta:
        verbose_name = 'Archiv dítěte'
        verbose_name_plural = 'Archiv dětí'
        ordering = ('-created_at', '-id')

    def __str__(self):
        return f"{self.child} | {self.created_at:%d.%m.%Y %H:%M} | {self.message[:40]}"


class ChildConsent(models.Model):
    SOURCE_REGISTRATION = 'registration'
    SOURCE_COMPLETION = 'completion'
    SOURCE_CHOICES = [
        (SOURCE_REGISTRATION, 'Registrace'),
        (SOURCE_COMPLETION, 'Doplnění údajů'),
    ]

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='consents', verbose_name='Dítě')
    parent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='child_consents')
    consent_vop = models.BooleanField(default=False, verbose_name='VOP')
    consent_gdpr = models.BooleanField(default=False, verbose_name='GDPR')
    consent_health = models.BooleanField(default=False, verbose_name='Zdravotní stav')
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_REGISTRATION, verbose_name='Zdroj')
    accepted_at = models.DateTimeField(default=timezone.now, verbose_name='Potvrzeno')

    class Meta:
        verbose_name = 'Souhlas dítěte'
        verbose_name_plural = 'Souhlasy dětí'
        ordering = ('-accepted_at', '-id')

    def __str__(self):
        return f"{self.child} | {self.accepted_at:%d.%m.%Y %H:%M}"


class ReceivedPayment(models.Model):
    received_date = models.DateField(default=timezone.localdate, verbose_name='Datum přijetí')
    variable_symbol = models.CharField(max_length=20, db_index=True, verbose_name='Variabilní symbol')
    amount_czk = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Částka (Kč)')
    sender_name = models.CharField(max_length=160, blank=True, verbose_name='Odesílatel')
    note = models.CharField(max_length=255, blank=True, verbose_name='Poznámka')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Vytvořeno')

    class Meta:
        verbose_name = 'Přijatá platba'
        verbose_name_plural = 'Přijaté platby'
        ordering = ('-received_date', '-id')

    def __str__(self):
        return f"{self.received_date:%d.%m.%Y} | VS {self.variable_symbol} | {self.amount_czk} Kč"


class SaleCharge(models.Model):
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='sale_charges', verbose_name='Dítě')
    title = models.CharField(max_length=160, verbose_name='Položka')
    amount_czk = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Částka (Kč)')
    note = models.CharField(max_length=255, blank=True, verbose_name='Poznámka')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_sale_charges',
        verbose_name='Vytvořil',
    )
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Vytvořeno')

    class Meta:
        verbose_name = 'Prodejní položka'
        verbose_name_plural = 'Prodejní položky'
        ordering = ('-created_at', '-id')

    def __str__(self):
        return f"{self.child} | {self.title} | {self.amount_czk} Kč"


class ClubDocument(models.Model):
    title = models.CharField(max_length=160, verbose_name='Název dokumentu')
    file = models.FileField(
        upload_to='documents/',
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png'])],
        verbose_name='Soubor',
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_club_documents',
        verbose_name='Nahrál',
    )
    uploaded_at = models.DateTimeField(default=timezone.now, verbose_name='Nahráno')

    class Meta:
        verbose_name = 'Dokument'
        verbose_name_plural = 'Dokumenty'
        ordering = ('-uploaded_at', '-id')

    def __str__(self):
        return self.title


class ChildFinanceEntry(models.Model):
    TYPE_PROFORMA = 'proforma'
    TYPE_INVOICE = 'invoice'
    TYPE_PAYMENT = 'payment'
    TYPE_SALE = 'sale'
    TYPE_REFUND = 'refund'
    TYPE_MEMBERSHIP_END = 'membership_end'
    TYPE_INFO = 'info'
    TYPE_CHOICES = [
        (TYPE_PROFORMA, 'Záloha'),
        (TYPE_INVOICE, 'Faktura'),
        (TYPE_PAYMENT, 'Příchozí platba'),
        (TYPE_SALE, 'Prodejní položka'),
        (TYPE_REFUND, 'Vratka'),
        (TYPE_MEMBERSHIP_END, 'Ukončení členství'),
        (TYPE_INFO, 'Info'),
    ]

    DIR_DEBIT = 'debit'
    DIR_CREDIT = 'credit'
    DIRECTION_CHOICES = [
        (DIR_DEBIT, 'K úhradě'),
        (DIR_CREDIT, 'Příjem / výdej'),
    ]

    STATUS_OPEN = 'open'
    STATUS_CLOSED = 'closed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'Otevřeno'),
        (STATUS_CLOSED, 'Uzavřeno'),
        (STATUS_CANCELLED, 'Storno'),
    ]

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='finance_entries', verbose_name='Dítě')
    membership = models.ForeignKey(
        Membership,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='finance_entries',
        verbose_name='Členství',
    )
    event_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_INFO, verbose_name='Typ')
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default=DIR_DEBIT, verbose_name='Směr')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_OPEN, verbose_name='Stav')
    title = models.CharField(max_length=180, verbose_name='Popis')
    amount_czk = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name='Částka (Kč)')
    variable_symbol = models.CharField(max_length=20, blank=True, verbose_name='Variabilní symbol')
    reference_code = models.CharField(max_length=50, blank=True, verbose_name='Referenční číslo')
    note = models.TextField(blank=True, verbose_name='Poznámka')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='child_finance_entries',
        verbose_name='Vytvořil',
    )
    occurred_on = models.DateField(default=timezone.localdate, verbose_name='Datum události')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Vytvořeno')

    class Meta:
        verbose_name = 'Finance dítěte'
        verbose_name_plural = 'Finance dětí'
        ordering = ('-occurred_on', '-id')

    def __str__(self):
        return f"{self.child} | {self.title} | {self.amount_czk} Kč"
