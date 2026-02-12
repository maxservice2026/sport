from datetime import date
import random
import unicodedata

from django.core.management.base import BaseCommand
from django.db import transaction

from clubs.models import AttendanceOption, Child, Group, Membership, Sport, TrainerGroup
from users.models import User


class Command(BaseCommand):
    help = "Vytvori rozsahla testovaci data pro skupiny a cleny."

    SPORT_CONFIG = {
        "Fotbal": {
            "groups": {"U7": 15, "U9": 15, "U11": 15, "U13": 15},
            "training_days": ["Po", "St"],
            "options": [("1x tydne", 1, 1900), ("2x tydne", 2, 3200)],
            "trainer_count": 3,
        },
        "Atletika": {
            "groups": {"A1": 16, "A2": 16, "B1": 16, "B2": 16, "C1": 16, "C2": 16, "D1": 16},
            "training_days": ["Po", "Út", "Čt"],
            "options": [("2x tydne", 2, 2500), ("3x tydne", 3, 3400)],
            "trainer_count": 4,
        },
        "Gymnastika": {
            "groups": {"G-mix": 10, "G-závodní": 10},
            "training_days": ["Út", "Čt"],
            "options": [("1x tydne", 1, 1800), ("2x tydne", 2, 2900)],
            "trainer_count": 2,
        },
    }
    MARKER_GROUP_NAME = "TEST-SMAZAT"

    FIRST_NAMES = [
        "Adam",
        "Bara",
        "Cyril",
        "David",
        "Ema",
        "Filip",
        "Gabriela",
        "Honza",
        "Igor",
        "Jana",
        "Klara",
        "Lukas",
        "Marek",
        "Natalie",
        "Ondra",
        "Petra",
        "Radek",
        "Sabina",
        "Tomas",
        "Veronika",
    ]
    LAST_NAMES = [
        "Novak",
        "Svoboda",
        "Dvorak",
        "Cerny",
        "Prochazka",
        "Kucera",
        "Vesely",
        "Horak",
        "Nemec",
        "Pokorny",
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="trainer123",
            help="Heslo pro vytvorene trenery (default trainer123).",
        )

    def _create_or_update_trainer(self, sport_slug, order, password):
        email = f"trener.{sport_slug}{order}@seed.skmnisecko.test"
        trainer, created = User.objects.get_or_create(
            email=email,
            defaults={
                "role": "trainer",
                "first_name": f"Trener{order}",
                "last_name": sport_slug.capitalize(),
            },
        )
        if created:
            trainer.set_password(password)
            trainer.save(update_fields=["password"])
        return trainer, created

    def _create_or_update_parent(self, idx):
        email = f"rodic{idx:04d}@seed.skmnisecko.test"
        parent, created = User.objects.get_or_create(
            email=email,
            defaults={
                "role": "parent",
                "first_name": f"Rodic{idx}",
                "last_name": "Test",
                "phone": f"777{idx:06d}"[-9:],
                "street": f"Ulice {idx}",
                "city": "Mnisek pod Brdy",
                "zip_code": "25210",
            },
        )
        if created:
            parent.set_password("parent123")
            parent.save(update_fields=["password"])
        return parent, created

    def _child_birth_number(self, idx):
        # Deterministicky a unikatne v ramci seedu.
        return f"{idx:06d}/{1000 + (idx % 9000):04d}"

    def _slugify(self, value):
        ascii_only = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        return ascii_only.lower().replace(" ", "").replace("-", "")

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(42)
        start_date = date(2026, 2, 1)
        end_date = date(2026, 6, 30)
        trainer_password = options["password"]

        created_groups = 0
        created_children = 0
        created_memberships = 0
        created_trainers = 0
        child_idx = 1

        for sport_name, cfg in self.SPORT_CONFIG.items():
            sport, _ = Sport.objects.get_or_create(name=sport_name)

            sport_slug = self._slugify(sport_name)
            trainers = []
            for i in range(1, cfg["trainer_count"] + 1):
                trainer, was_created = self._create_or_update_trainer(sport_slug, i, trainer_password)
                if was_created:
                    created_trainers += 1
                trainers.append(trainer)

            for group_order, (group_name, member_count) in enumerate(cfg["groups"].items()):
                group, was_created = Group.objects.get_or_create(
                    sport=sport,
                    name=group_name,
                    defaults={
                        "training_days": cfg["training_days"],
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                )
                if was_created:
                    created_groups += 1
                else:
                    group.training_days = cfg["training_days"]
                    group.start_date = start_date
                    group.end_date = end_date
                    group.save(update_fields=["training_days", "start_date", "end_date"])

                option_objs = []
                for opt_name, freq, price in cfg["options"]:
                    option, _ = AttendanceOption.objects.get_or_create(
                        group=group,
                        name=opt_name,
                        defaults={"frequency_per_week": freq, "price_czk": price},
                    )
                    if option.frequency_per_week != freq or option.price_czk != price:
                        option.frequency_per_week = freq
                        option.price_czk = price
                        option.save(update_fields=["frequency_per_week", "price_czk"])
                    option_objs.append(option)

                assigned_trainer = trainers[group_order % len(trainers)]
                TrainerGroup.objects.get_or_create(trainer=assigned_trainer, group=group)

                for _ in range(member_count):
                    parent_idx = ((child_idx - 1) // 2) + 1
                    parent, _ = self._create_or_update_parent(parent_idx)
                    first_name = self.FIRST_NAMES[(child_idx - 1) % len(self.FIRST_NAMES)]
                    last_name = self.LAST_NAMES[((child_idx - 1) // len(self.FIRST_NAMES)) % len(self.LAST_NAMES)]
                    birth_number = self._child_birth_number(child_idx)

                    child, child_created = Child.objects.get_or_create(
                        birth_number=birth_number,
                        defaults={
                            "parent": parent,
                            "first_name": first_name,
                            "last_name": last_name,
                            "phone": "",
                        },
                    )
                    if child_created:
                        created_children += 1
                    elif child.parent_id != parent.id:
                        child.parent = parent
                        child.save(update_fields=["parent"])

                    option = option_objs[rng.randint(0, len(option_objs) - 1)]
                    _, membership_created = Membership.objects.get_or_create(
                        child=child,
                        group=group,
                        defaults={"attendance_option": option},
                    )
                    if membership_created:
                        created_memberships += 1
                    child_idx += 1

        # Central marker group for easy filtering and later cleanup
        atletika = Sport.objects.get(name="Atletika")
        marker_group, _ = Group.objects.get_or_create(
            sport=atletika,
            name=self.MARKER_GROUP_NAME,
            defaults={
                "training_days": [],
                "start_date": start_date,
                "end_date": end_date,
            },
        )

        marked_children = 0
        marker_memberships = 0
        test_children = Child.objects.filter(parent__email__iendswith="@seed.skmnisecko.test").select_related("parent")
        for child in test_children:
            if not child.first_name.startswith("TEST "):
                child.first_name = f"TEST {child.first_name}"
                child.save(update_fields=["first_name"])
                marked_children += 1

            _, marker_created = Membership.objects.get_or_create(
                child=child,
                group=marker_group,
                defaults={"attendance_option": None},
            )
            if marker_created:
                marker_memberships += 1

        self.stdout.write(self.style.SUCCESS("Testovaci data byla pripravena."))
        self.stdout.write(f"Vytvoreni novi treneri: {created_trainers}")
        self.stdout.write(f"Vytvorene nove skupiny: {created_groups}")
        self.stdout.write(f"Vytvorene nove deti: {created_children}")
        self.stdout.write(f"Vytvorena nova clenstvi: {created_memberships}")
        self.stdout.write(f"Oznacene test deti: {marked_children}")
        self.stdout.write(f"Prirazeni do skupiny {self.MARKER_GROUP_NAME}: {marker_memberships}")
        self.stdout.write("")
        self.stdout.write("Treneri maji heslo: " + trainer_password)
        self.stdout.write("Rodice maji heslo: parent123")
