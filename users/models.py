from django.contrib.auth.models import AbstractUser
from django.db import models
from .managers import UserManager
from django.utils import timezone
from tenants.scoping import TenantScopedManager


class User(AbstractUser):
    # Synthetic username = "<tenant_slug>:<email>". Keeps Django auth happy (USERNAME_FIELD must be unique)
    # while allowing same email in different tenants.
    username = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        editable=False,
        verbose_name='Uživatelské jméno',
    )
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='users',
        verbose_name='Tenant',
    )
    email = models.EmailField(unique=False, verbose_name='Email')

    ROLE_ADMIN = 'admin'
    ROLE_TRAINER = 'trainer'
    ROLE_PARENT = 'parent'
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Administrátor'),
        (ROLE_TRAINER, 'Trenér'),
        (ROLE_PARENT, 'Rodič'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_PARENT, verbose_name='Role')

    PAYMENT_DPP = 'dpp'
    PAYMENT_INVOICE = 'invoice'
    PAYMENT_CHOICES = [
        (PAYMENT_DPP, 'DPP'),
        (PAYMENT_INVOICE, 'Faktura'),
    ]

    phone = models.CharField(max_length=30, blank=True, verbose_name='Telefon')
    street = models.CharField(max_length=120, blank=True, verbose_name='Ulice')
    city = models.CharField(max_length=80, blank=True, verbose_name='Město')
    zip_code = models.CharField(max_length=10, blank=True, verbose_name='PSČ')
    trainer_payment_mode = models.CharField(
        max_length=20,
        choices=PAYMENT_CHOICES,
        default=PAYMENT_DPP,
        verbose_name='Typ odměny trenéra',
    )
    trainer_tax_15_enabled = models.BooleanField(default=False, verbose_name='Daň 15 %')
    trainer_rate_per_record = models.PositiveIntegerField(default=0, verbose_name='Částka za 1 záznam (Kč)')

    class Meta:
        verbose_name = 'Uživatel'
        verbose_name_plural = 'Uživatelé'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'email'], name='users_user_tenant_email_uniq'),
        ]

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']

    objects = UserManager()
    all_objects = models.Manager()

    def __str__(self):
        return f"{self.email} ({self.get_role_display()})"

    @staticmethod
    def build_username(tenant_slug: str, email: str) -> str:
        return f"{(tenant_slug or 'default').strip().lower()}:{(email or '').strip().lower()}"

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            from tenants.threadlocal import get_current_tenant

            self.tenant = get_current_tenant()
        if self.tenant and self.email:
            self.username = self.build_username(self.tenant.slug, self.email)
        super().save(*args, **kwargs)


class EconomyExpense(models.Model):
    TYPE_GENERAL = 'general'
    TYPE_TRAINER_SERVICE = 'trainer_service'
    TYPE_TRAINER_REIMBURSEMENT = 'trainer_reimbursement'
    TYPE_CHOICES = [
        (TYPE_GENERAL, 'Náklad klubu'),
        (TYPE_TRAINER_SERVICE, 'Trenérská služba (do mzdy)'),
        (TYPE_TRAINER_REIMBURSEMENT, 'Proplacení nákupu (mimo mzdu)'),
    ]

    expense_date = models.DateField(default=timezone.now, verbose_name='Datum')
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='economy_expenses',
        verbose_name='Tenant',
    )
    title = models.CharField(max_length=160, verbose_name='Název nákladu')
    amount_czk = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Částka (Kč)')
    expense_type = models.CharField(
        max_length=30,
        choices=TYPE_CHOICES,
        default=TYPE_GENERAL,
        verbose_name='Typ nákladu',
    )
    trainer = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='trainer_expenses',
        limit_choices_to={'role': 'trainer'},
        verbose_name='Trenér',
    )
    note = models.CharField(max_length=255, blank=True, verbose_name='Poznámka')
    recurring_source = models.ForeignKey(
        'users.EconomyRecurringExpense',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='generated_expenses',
        verbose_name='Zdroj opakování',
    )
    created_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_expenses',
        verbose_name='Vytvořil',
    )
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Vytvořeno')

    class Meta:
        verbose_name = 'Náklad'
        verbose_name_plural = 'Náklady'
        ordering = ('-expense_date', '-id')

    objects = TenantScopedManager('tenant')
    all_objects = models.Manager()

    def __str__(self):
        return f"{self.expense_date:%d.%m.%Y} - {self.title} ({self.amount_czk} Kč)"

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            from tenants.threadlocal import get_current_tenant

            self.tenant = get_current_tenant()
        super().save(*args, **kwargs)


class EconomyRecurringExpense(models.Model):
    RECUR_WEEKLY = 'weekly'
    RECUR_14_DAYS = '14days'
    RECUR_MONTHLY = 'monthly'
    RECUR_QUARTERLY = 'quarterly'
    RECUR_YEARLY = 'yearly'
    RECUR_CHOICES = [
        (RECUR_WEEKLY, 'Týdně'),
        (RECUR_14_DAYS, '14 dní'),
        (RECUR_MONTHLY, 'Měsíčně'),
        (RECUR_QUARTERLY, 'Kvartálně'),
        (RECUR_YEARLY, 'Ročně'),
    ]

    title = models.CharField(max_length=160, verbose_name='Název nákladu')
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='economy_recurring_expenses',
        verbose_name='Tenant',
    )
    amount_czk = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Částka (Kč)')
    note = models.CharField(max_length=255, blank=True, verbose_name='Poznámka')
    recurrence = models.CharField(max_length=20, choices=RECUR_CHOICES, verbose_name='Opakování')
    start_date = models.DateField(default=timezone.now, verbose_name='Začátek')
    next_run_date = models.DateField(verbose_name='Další zaúčtování')
    active = models.BooleanField(default=True, verbose_name='Aktivní')
    created_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_recurring_expenses',
        verbose_name='Vytvořil',
    )
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Vytvořeno')

    class Meta:
        verbose_name = 'Opakovaný náklad'
        verbose_name_plural = 'Opakované náklady'
        ordering = ('title', 'id')

    objects = TenantScopedManager('tenant')
    all_objects = models.Manager()

    def __str__(self):
        return f"{self.title} ({self.get_recurrence_display()})"

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            from tenants.threadlocal import get_current_tenant

            self.tenant = get_current_tenant()
        super().save(*args, **kwargs)


class AppSettings(models.Model):
    PAYMENT_EMAIL_CUSTOM = 'custom'
    PAYMENT_EMAIL_FORWARD = 'forward'
    PAYMENT_EMAIL_MODE_CHOICES = [
        (PAYMENT_EMAIL_CUSTOM, 'IMAP/SMTP'),
        (PAYMENT_EMAIL_FORWARD, 'Přesměrování na klubový email'),
    ]

    tenant = models.OneToOneField(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='app_settings',
        verbose_name='Tenant',
    )
    primary_color = models.CharField(max_length=20, default='#1e5f8f', verbose_name='Primární barva')
    secondary_color = models.CharField(max_length=20, default='#5f6570', verbose_name='Sekundární barva')
    accent_color = models.CharField(max_length=20, default='#c62828', verbose_name='Akcent / varování')

    consent_vop_text = models.TextField(blank=True, verbose_name='VOP')
    consent_gdpr_text = models.TextField(blank=True, verbose_name='GDPR')
    consent_health_text = models.TextField(blank=True, verbose_name='Prohlášení o zdravotním stavu')
    registration_confirmation_subject = models.CharField(
        max_length=200,
        default='SK MNÍŠECKO - potvrzujeme přijetí registrace',
        verbose_name='Potvrzení přijetí registrace - předmět',
    )
    registration_confirmation_body = models.TextField(
        default='Dobrý den, děkujeme za zaslání registrace. Tým SK Mníšecko.',
        verbose_name='Potvrzení přijetí registrace - text',
    )
    welcome_subject = models.CharField(
        max_length=200,
        default='SK MNÍŠECKO - Vítejte v SK Mníšecko',
        verbose_name='Vítejte - předmět',
    )
    welcome_body = models.TextField(
        default='Dobrý den, vítejte v SK Mníšecko.',
        verbose_name='Vítejte - text',
    )

    payment_email_mode = models.CharField(
        max_length=20,
        choices=PAYMENT_EMAIL_MODE_CHOICES,
        default=PAYMENT_EMAIL_CUSTOM,
        verbose_name='Režim emailu plateb',
    )
    payment_imap_host = models.CharField(max_length=120, blank=True, verbose_name='IMAP host')
    payment_imap_port = models.PositiveIntegerField(default=993, verbose_name='IMAP port')
    payment_imap_user = models.CharField(max_length=160, blank=True, verbose_name='IMAP uživatel')
    payment_imap_password = models.CharField(max_length=255, blank=True, verbose_name='IMAP heslo')
    payment_smtp_host = models.CharField(max_length=120, blank=True, verbose_name='SMTP host')
    payment_smtp_port = models.PositiveIntegerField(default=587, verbose_name='SMTP port')
    payment_smtp_user = models.CharField(max_length=160, blank=True, verbose_name='SMTP uživatel')
    payment_smtp_password = models.CharField(max_length=255, blank=True, verbose_name='SMTP heslo')
    payment_forward_email = models.EmailField(blank=True, verbose_name='Přesměrovací email')

    created_at = models.DateTimeField(default=timezone.now, verbose_name='Vytvořeno')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Aktualizováno')

    class Meta:
        verbose_name = 'Nastavení aplikace'
        verbose_name_plural = 'Nastavení aplikace'

    objects = TenantScopedManager('tenant')
    all_objects = models.Manager()

    def __str__(self):
        return f"Nastavení aplikace ({self.tenant.slug if self.tenant else 'bez tenant'})"

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            from tenants.threadlocal import get_current_tenant

            self.tenant = get_current_tenant()
        super().save(*args, **kwargs)
