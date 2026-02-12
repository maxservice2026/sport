from django.db import migrations, models
from django.utils import timezone
import uuid


def backfill_child_identifiers(apps, schema_editor):
    Child = apps.get_model('clubs', 'Child')
    for child in Child.objects.all():
        updates = []
        if not getattr(child, 'created_at', None):
            child.created_at = timezone.now()
            updates.append('created_at')
        if not getattr(child, 'unique_id', None):
            child.unique_id = uuid.uuid4()
            updates.append('unique_id')
        if not getattr(child, 'variable_symbol', None):
            date_part = child.created_at.strftime('%Y%m%d')
            child.variable_symbol = f"{date_part}{child.id:05d}"
            updates.append('variable_symbol')
        if updates:
            child.save(update_fields=updates)


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('clubs', '0003_alter_attendanceoption_options_alter_child_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='child',
            name='created_at',
            field=models.DateTimeField(default=timezone.now, editable=False, verbose_name='Vytvořeno'),
        ),
        migrations.AddField(
            model_name='child',
            name='unique_id',
            field=models.UUIDField(editable=False, null=True, verbose_name='Unikátní ID'),
        ),
        migrations.AddField(
            model_name='child',
            name='variable_symbol',
            field=models.CharField(blank=True, max_length=20, null=True, verbose_name='Variabilní symbol'),
        ),
        migrations.RunPython(backfill_child_identifiers, reverse_noop),
        migrations.AlterField(
            model_name='child',
            name='unique_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='Unikátní ID'),
        ),
        migrations.AlterField(
            model_name='child',
            name='variable_symbol',
            field=models.CharField(blank=True, max_length=20, unique=True, verbose_name='Variabilní symbol'),
        ),
    ]
