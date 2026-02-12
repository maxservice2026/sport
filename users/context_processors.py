from .utils import get_app_settings


def app_ui(request):
    settings_obj = get_app_settings()
    can_use_django_admin = bool(
        getattr(request, 'user', None)
        and request.user.is_authenticated
        and request.user.email.lower() == 'jirka@sportujpodbrdy.cz'
    )
    return {
        'app_settings': settings_obj,
        'can_use_django_admin': can_use_django_admin,
    }
