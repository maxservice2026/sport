from django.db import models
from django.utils import timezone


class Tenant(models.Model):
    """
    Tenant (klub/instance) pro multitenant provoz v jedné DB.
    Identifikátor tenantu je `slug` (např. "dipoli"), který vybíráme z URL parametru `?tenant=...`
    (a následně ukládáme do cookie, aby nebylo nutné parametr přidávat do všech URL).
    """

    slug = models.SlugField(max_length=50, unique=True, verbose_name='Slug')
    name = models.CharField(max_length=120, verbose_name='Název')
    active = models.BooleanField(default=True, verbose_name='Aktivní')

    # Per-tenant sekvence pro VS dítěte (max 4 číslice dle požadavku).
    next_child_vs = models.PositiveIntegerField(default=1, verbose_name='Další VS dítěte')

    created_at = models.DateTimeField(default=timezone.now, verbose_name='Vytvořeno')

    class Meta:
        verbose_name = 'Tenant'
        verbose_name_plural = 'Tenanti'
        ordering = ('slug',)

    def __str__(self):
        return f"{self.slug} ({self.name})"
