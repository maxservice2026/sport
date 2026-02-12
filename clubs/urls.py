from django.urls import path
from . import views

urlpatterns = [
    path('registrace/', views.public_register, name='public_register'),
    path('doplnit-udaje/', views.public_data_completion, name='public_data_completion'),
    path('api/attendance-options/', views.attendance_options_api, name='attendance_options_api'),
    path('admin/groups/', views.admin_group_list, name='admin_groups'),
    path('admin/groups/new/', views.admin_group_create, name='admin_group_create'),
    path('admin/groups/<int:group_id>/', views.admin_group_detail, name='admin_group_detail'),
    path('admin/groups/<int:group_id>/edit/', views.admin_group_edit, name='admin_group_edit'),
    path('admin/children/', views.admin_children_list, name='admin_children'),
    path('admin/children/export.xls', views.admin_children_export_xls, name='admin_children_export_xls'),
    path('admin/children/<int:child_id>/', views.admin_child_edit, name='admin_child_edit'),
    path('admin/contributions/', views.admin_contributions, name='admin_contributions'),
    path('admin/payments/', views.admin_received_payments, name='admin_received_payments'),
    path('admin/documents/', views.admin_documents, name='admin_documents'),
]
