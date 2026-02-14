from django.db import models
from django.utils import timezone
from django.conf import settings
from clubs.models import Group, Child
from tenants.scoping import TenantScopedManager


class TrainingSession(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='sessions', verbose_name='Skupina')
    date = models.DateField(verbose_name='Datum')
    is_extra = models.BooleanField(default=False, verbose_name='Mimo pravidelný rozvrh')
    is_cancelled = models.BooleanField(default=False, verbose_name='Zrušený trénink')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Vytvořeno')

    class Meta:
        unique_together = ('group', 'date')
        verbose_name = 'Tréninkový den'
        verbose_name_plural = 'Tréninkové dny'

    def __str__(self):
        return f"{self.group} - {self.date}"

    objects = TenantScopedManager('group__tenant')
    all_objects = models.Manager()


class Attendance(models.Model):
    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name='attendance_records', verbose_name='Tréninkový den')
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='attendance_records', verbose_name='Dítě')
    present = models.BooleanField(default=True, verbose_name='Přítomen')
    recorded_at = models.DateTimeField(default=timezone.now, verbose_name='Zaznamenáno')

    class Meta:
        unique_together = ('session', 'child')
        verbose_name = 'Docházka'
        verbose_name_plural = 'Docházka'

    def __str__(self):
        return f"{self.child} - {self.session.date}: {'Přítomen' if self.present else 'Nepřítomen'}"

    objects = TenantScopedManager('session__group__tenant')
    all_objects = models.Manager()


class TrainerAttendance(models.Model):
    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name='trainer_attendance_records', verbose_name='Tréninkový den')
    trainer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='trainer_attendance_records',
        limit_choices_to={'role': 'trainer'},
        verbose_name='Trenér',
    )
    present = models.BooleanField(default=True, verbose_name='Přítomen')
    extra_access = models.BooleanField(default=False, verbose_name='Mimo přiřazenou skupinu')
    recorded_at = models.DateTimeField(default=timezone.now, verbose_name='Zaznamenáno')

    class Meta:
        unique_together = ('session', 'trainer')
        verbose_name = 'Docházka trenéra'
        verbose_name_plural = 'Docházka trenérů'

    def __str__(self):
        return f"{self.trainer} - {self.session.date}: {'Přítomen' if self.present else 'Nepřítomen'}"

    objects = TenantScopedManager('session__group__tenant')
    all_objects = models.Manager()
