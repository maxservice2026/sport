from django.core.management.base import BaseCommand
from clubs.models import Sport


class Command(BaseCommand):
    help = 'Vytvoří základní sporty (atletika, fotbal, gymnastika).'

    def handle(self, *args, **options):
        sports = ['Atletika', 'Fotbal', 'Gymnastika']
        created = 0
        for name in sports:
            obj, was_created = Sport.objects.get_or_create(name=name)
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(f'Hotovo. Nově vytvořeno: {created}'))
