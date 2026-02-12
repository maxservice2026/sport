from django.contrib import admin
from django.urls import path, include
from users import views as user_views

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
