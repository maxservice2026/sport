from django.urls import path
from . import views

urlpatterns = [
    path('admin/', views.admin_dashboard, name='admin_dashboard_root'),
    path('admin/dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('admin/trainers/', views.admin_trainer_list, name='admin_trainers'),
    path('admin/trainers/new/', views.admin_trainer_create, name='admin_trainer_create'),
    path('admin/trainers/<int:user_id>/', views.admin_trainer_edit, name='admin_trainer_edit'),
    path('admin/economics/', views.admin_economics, name='admin_economics'),
    path('admin/settings/', views.admin_settings, name='admin_settings'),
    path('trainer/', views.trainer_dashboard, name='trainer_dashboard'),
    path('trainer/economics/', views.trainer_economics, name='trainer_economics'),
    path('reset-hesla/', views.UserPasswordResetView.as_view(), name='password_reset'),
    path('reset-hesla/odeslano/', views.UserPasswordResetDoneView.as_view(), name='password_reset_done'),
    path('reset-hesla/<uidb64>/<token>/', views.UserPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('reset-hesla/hotovo/', views.UserPasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('parent/', views.parent_dashboard, name='parent_dashboard'),
    path('parent/profile/', views.parent_profile, name='parent_profile'),
    path('parent/child/<int:child_id>/', views.parent_child_detail, name='parent_child_detail'),
    path('parent/proforma/<int:entry_id>/', views.parent_proforma_detail, name='parent_proforma_detail'),
]
