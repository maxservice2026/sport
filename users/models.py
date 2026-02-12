from django.contrib.auth.models import AbstractUser
from django.db import models
from .managers import UserManager
from django.utils import timezone


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True, verbose_name='Email')

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

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return f"{self.email} ({self.get_role_display()})"


class EconomyExpense(models.Model):
    expense_date = models.DateField(default=timezone.now, verbose_name='Datum')
    title = models.CharField(max_length=160, verbose_name='Název nákladu')
    amount_czk = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Částka (Kč)')
    note = models.CharField(max_length=255, blank=True, verbose_name='Poznámka')
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

    def __str__(self):
        return f"{self.expense_date:%d.%m.%Y} - {self.title} ({self.amount_czk} Kč)"
