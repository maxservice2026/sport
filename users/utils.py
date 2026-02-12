from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect
from functools import wraps

from .models import AppSettings


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
    settings_obj = AppSettings.objects.order_by('id').first()
    if settings_obj:
        return settings_obj
    return AppSettings.objects.create()
