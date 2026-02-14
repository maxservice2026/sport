from django.core.management.base import BaseCommand
from clubs.models import Sport
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Vytvoří základní sporty (atletika, fotbal, gymnastika).'

    def handle(self, *args, **options):
        tenant, _ = Tenant.objects.get_or_create(slug='default', defaults={'name': 'Hlavní tenant', 'active': True})
        sports = ['Atletika', 'Fotbal', 'Gymnastika']
        created = 0
        for name in sports:
            obj, was_created = Sport.objects.get_or_create(tenant=tenant, name=name)
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(f'Hotovo. Nově vytvořeno: {created}'))
