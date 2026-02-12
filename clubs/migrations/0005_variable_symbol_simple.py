from django.db import migrations, models


def resequence_variable_symbols(apps, schema_editor):
    Child = apps.get_model('clubs', 'Child')
    children = Child.objects.order_by('created_at', 'id')
    for idx, child in enumerate(children, start=1):
        if idx > 9999:
            raise RuntimeError('Nelze vytvořit 4místný variabilní symbol pro více než 9999 dětí.')
        child.variable_symbol = str(idx)
        child.save(update_fields=['variable_symbol'])


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('clubs', '0004_child_identifiers'),
    ]

    operations = [
        migrations.RunPython(resequence_variable_symbols, reverse_noop),
        migrations.AlterField(
            model_name='child',
            name='variable_symbol',
            field=models.CharField(blank=True, max_length=4, unique=True, verbose_name='Variabilní symbol'),
        ),
    ]
