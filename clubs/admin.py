from django.contrib import admin
from .models import Sport, Group, AttendanceOption, Child, Membership, TrainerGroup


@admin.register(Sport)
class SportAdmin(admin.ModelAdmin):
    list_display = ['name']


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'sport']
    list_filter = ['sport']
    search_fields = ['name']


@admin.register(AttendanceOption)
class AttendanceOptionAdmin(admin.ModelAdmin):
    list_display = ['name', 'group', 'frequency_per_week', 'price_czk']
    list_filter = ['group']


@admin.register(Child)
class ChildAdmin(admin.ModelAdmin):
    list_display = ['first_name', 'last_name', 'birth_number', 'passport_number', 'parent']
    search_fields = ['first_name', 'last_name', 'birth_number', 'passport_number']


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ['child', 'group', 'attendance_option', 'active', 'registered_at']
    list_filter = ['group', 'active']


@admin.register(TrainerGroup)
class TrainerGroupAdmin(admin.ModelAdmin):
    list_display = ['trainer', 'group']
    list_filter = ['group']
