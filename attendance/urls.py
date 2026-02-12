from django.urls import path
from . import views

urlpatterns = [
    path('trainer/group/<int:group_id>/attendance/', views.trainer_attendance, name='trainer_attendance'),
    path('admin/attendance/', views.admin_attendance, name='admin_attendance'),
]
