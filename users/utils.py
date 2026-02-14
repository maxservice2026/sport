from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect
from functools import wraps

from .models import AppSettings
from tenants.threadlocal import get_current_tenant


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
                messages.error(request, 'Nemáte oprávnění pro tuto stránku.')
                return redirect('home')
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def get_app_settings():
    tenant = get_current_tenant()
    qs = AppSettings.objects.order_by('id')
    if tenant:
        qs = qs.filter(tenant=tenant)
    settings_obj = qs.first()
    if settings_obj:
        return settings_obj
    if tenant:
        return AppSettings.objects.create(tenant=tenant)
    return AppSettings.objects.create()
