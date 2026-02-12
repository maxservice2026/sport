from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from users import views as user_views


def _restricted_admin_has_permission(request):
    user = request.user
    return bool(
        user
        and user.is_active
        and user.is_staff
        and user.email.lower() == 'jirka@sportujpodbrdy.cz'
    )


admin.site.has_permission = _restricted_admin_has_permission

urlpatterns = [
    # Custom admin dashboard must be resolved before Django admin catch-all.
    path('', include('users.urls')),
    path('login/', user_views.login_view, name='login'),
    path('logout/', user_views.logout_view, name='logout'),
    path('', user_views.home, name='home'),
    path('', include('clubs.urls')),
    path('', include('attendance.urls')),
    path('django-admin/', admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
