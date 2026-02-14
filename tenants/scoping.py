from django.db import models

from .threadlocal import get_current_tenant


class TenantScopedManager(models.Manager):
    """
    Manager, který automaticky filtruje queryset podle aktuálního tenantu.

    tenant_path:
      - 'tenant' (přímé FK tenant)
      - nebo cesta přes relace, např. 'group__tenant', 'child__tenant', 'session__group__tenant'
    """

    def __init__(self, tenant_path='tenant', *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant_path = tenant_path

    def get_queryset(self):
        qs = super().get_queryset()
        tenant = get_current_tenant()
        if not tenant:
            return qs
        return qs.filter(**{self.tenant_path: tenant})

