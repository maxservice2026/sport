from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from users.models import User
from clubs.models import Sport, Group, AttendanceOption, Child, Membership, TrainerGroup
from attendance.models import TrainingSession, Attendance


class Command(BaseCommand):
    help = 'Vytvoří demo data (admin, trenér, rodič, skupina, děti).'

    def handle(self, *args, **options):
        with transaction.atomic():
            sport, _ = Sport.objects.get_or_create(name='Atletika')

            group, _ = Group.objects.get_or_create(
                sport=sport,
                name='Test skupina',
                defaults={'training_days': ['Po', 'St']},
            )

            option, _ = AttendanceOption.objects.get_or_create(
                group=group,
                name='2× týdně',
                defaults={'frequency_per_week': 2, 'price_czk': 1500},
            )

            admin, admin_created = User.objects.get_or_create(
                email='admin@skmnisecko.test',
                defaults={
                    'role': 'admin',
                    'first_name': 'Admin',
                    'last_name': 'Test',
                    'is_staff': True,
                    'is_superuser': True,
                },
            )
            if admin_created:
                admin.set_password('admin123')
                admin.save()

            trainer, trainer_created = User.objects.get_or_create(
                email='trener@skmnisecko.test',
                defaults={
                    'role': 'trainer',
                    'first_name': 'Trenér',
                    'last_name': 'Test',
                },
            )
            if trainer_created:
                trainer.set_password('trainer123')
                trainer.save()

            parent, parent_created = User.objects.get_or_create(
                email='rodic@skmnisecko.test',
                defaults={
                    'role': 'parent',
                    'first_name': 'Rodič',
                    'last_name': 'Test',
                    'phone': '777123456',
                    'street': 'Testovací 1',
                    'city': 'Mníšek pod Brdy',
                    'zip_code': '25210',
                },
            )
            if parent_created:
                parent.set_password('parent123')
                parent.save()

            TrainerGroup.objects.get_or_create(trainer=trainer, group=group)

            children_data = [
                ('Jan', 'Novák', '120101/1234'),
                ('Petra', 'Nováková', '130202/2345'),
                ('Marek', 'Novák', '140303/3456'),
            ]

            for first_name, last_name, birth_number in children_data:
                child, _ = Child.objects.get_or_create(
                    parent=parent,
                    birth_number=birth_number,
                    defaults={
                        'first_name': first_name,
                        'last_name': last_name,
                        'phone': '',
                    },
                )
                Membership.objects.get_or_create(
                    child=child,
                    group=group,
                    defaults={'attendance_option': option},
                )

            session, _ = TrainingSession.objects.get_or_create(
                group=group,
                date=timezone.now().date(),
            )
            first_child = Child.objects.filter(parent=parent).first()
            if first_child:
                Attendance.objects.get_or_create(session=session, child=first_child, defaults={'present': True})

        self.stdout.write(self.style.SUCCESS('Demo data byla vytvořena.'))
        self.stdout.write('Přihlašovací údaje:')
        self.stdout.write('  Admin  : admin@skmnisecko.test / admin123')
        self.stdout.write('  Trenér : trener@skmnisecko.test / trainer123')
        self.stdout.write('  Rodič  : rodic@skmnisecko.test / parent123')
