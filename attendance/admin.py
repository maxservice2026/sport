from django.contrib import admin
from .models import TrainingSession, Attendance, TrainerAttendance


@admin.register(TrainingSession)
class TrainingSessionAdmin(admin.ModelAdmin):
    list_display = ['group', 'date']
    list_filter = ['group']


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ['session', 'child', 'present', 'recorded_at']
    list_filter = ['session__group', 'present']


@admin.register(TrainerAttendance)
class TrainerAttendanceAdmin(admin.ModelAdmin):
    list_display = ['session', 'trainer', 'present', 'recorded_at']
    list_filter = ['session__group', 'present']
