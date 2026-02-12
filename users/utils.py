from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect
from functools import wraps


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
