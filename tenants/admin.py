from django.contrib import admin

from .models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'active', 'next_child_vs', 'created_at')
    list_filter = ('active',)
    search_fields = ('slug', 'name')
