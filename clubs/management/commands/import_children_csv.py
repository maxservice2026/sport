import csv
import re
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from clubs.models import Child, Group, Membership, Sport
from users.models import User


class Command(BaseCommand):
    help = "Importuje deti, skupiny a clenstvi z CSV (Skupina;Jmeno;Telefon;Rocnik;VS)."

    GROUP_SPORT_RULES = [
        (re.compile(r"^U\d+$", re.IGNORECASE), "Fotbal"),
        (re.compile(r"^GYM", re.IGNORECASE), "Gymnastika"),
    ]

    SPORT_DEFAULTS = {
        "Atletika": {"training_days": ["Po", "St"], "start_date": date(2026, 2, 1), "end_date": date(2026, 6, 30)},
        "Fotbal": {"training_days": ["Po", "St"], "start_date": date(2026, 2, 1), "end_date": date(2026, 6, 30)},
        "Gymnastika": {"training_days": ["Út", "Čt"], "start_date": date(2026, 2, 1), "end_date": date(2026, 6, 30)},
    }

    def add_arguments(self, parser):
        parser.add_argument("csv_path", help="Cesta k CSV souboru.")
        parser.add_argument("--encoding", default="utf-8-sig", help="Kódování souboru (default: utf-8-sig).")
        parser.add_argument("--delimiter", default=";", help="Oddělovač CSV (default: ;).")

    def _detect_sport_name(self, group_name):
        group_name = (group_name or "").strip()
        for pattern, sport_name in self.GROUP_SPORT_RULES:
            if pattern.search(group_name):
                return sport_name
        return "Atletika"

    def _normalize_phone(self, value):
        return "".join(ch for ch in (value or "") if ch.isdigit())

    def _split_child_name(self, raw_name):
        clean = re.sub(r"\s*\(.*\)\s*$", "", (raw_name or "").strip())
        tokens = [part for part in clean.split() if part]
        if not tokens:
            return "Neznámé", "Dítě"
        if len(tokens) == 1:
            return tokens[0], "-"
        return tokens[0], " ".join(tokens[1:])

    def _is_probably_female(self, first_name):
        lowered = (first_name or "").strip().lower()
        return lowered.endswith("a") or lowered.endswith("á")

    def _build_unique_birth_number(self, year, vs_int, first_name, current_child_id=None):
        # CSV obsahuje jen rocnik, proto vytvarime synteticke RČ:
        # DDMMYY/SSSS (s mesicem +50 pro pravdepodobne dívky).
        yy = year % 100
        female = self._is_probably_female(first_name)
        for salt in range(0, 5000):
            day = ((vs_int + salt) % 28) + 1
            month = (((vs_int // 28) + salt) % 12) + 1
            if female:
                month += 50
            suffix = ((vs_int * 13) + salt) % 10000
            candidate = f"{day:02d}{month:02d}{yy:02d}/{suffix:04d}"
            query = Child.objects.filter(birth_number=candidate)
            if current_child_id:
                query = query.exclude(pk=current_child_id)
            if not query.exists():
                return candidate
        return None

    def _monthly_start(self, value):
        return date(value.year, value.month, 1)

    @transaction.atomic
    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"]).expanduser()
        if not csv_path.exists():
            raise CommandError(f"Soubor neexistuje: {csv_path}")

        delimiter = options["delimiter"]
        encoding = options["encoding"]

        created_groups = 0
        created_parents = 0
        created_children = 0
        updated_children = 0
        created_memberships = 0
        skipped_rows = 0

        with csv_path.open("r", encoding=encoding, newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            required_columns = {"Skupina", "Jméno", "Telefon", "Ročník", "VS"}
            if not required_columns.issubset(set(reader.fieldnames or [])):
                raise CommandError(
                    f"CSV musi obsahovat sloupce: {', '.join(sorted(required_columns))}. "
                    f"Nalezeno: {reader.fieldnames}"
                )

            for row_idx, row in enumerate(reader, start=2):
                group_name = (row.get("Skupina") or "").strip()
                raw_name = (row.get("Jméno") or "").strip()
                phone = self._normalize_phone(row.get("Telefon"))
                year_raw = (row.get("Ročník") or "").strip()
                vs_raw = (row.get("VS") or "").strip()

                if not any([group_name, raw_name, phone, year_raw, vs_raw]):
                    continue

                if not group_name or not raw_name or not vs_raw:
                    skipped_rows += 1
                    self.stdout.write(self.style.WARNING(f"Řádek {row_idx}: přeskočeno (chybí skupina/jméno/VS)."))
                    continue
                if not vs_raw.isdigit() or len(vs_raw) > 4:
                    skipped_rows += 1
                    self.stdout.write(self.style.WARNING(f"Řádek {row_idx}: přeskočeno (neplatné VS: {vs_raw})."))
                    continue

                year = None
                if year_raw.isdigit():
                    parsed_year = int(year_raw)
                    if 1900 <= parsed_year <= 2100:
                        year = parsed_year

                sport_name = self._detect_sport_name(group_name)
                sport, _ = Sport.objects.get_or_create(name=sport_name)
                defaults = self.SPORT_DEFAULTS[sport_name]
                group, group_created = Group.objects.get_or_create(
                    sport=sport,
                    name=group_name,
                    defaults={
                        "training_days": defaults["training_days"],
                        "start_date": defaults["start_date"],
                        "end_date": defaults["end_date"],
                    },
                )
                if group_created:
                    created_groups += 1

                email_local = phone if phone else f"vs{vs_raw}"
                parent_email = f"import-{email_local}@import.skmnisecko.local"
                parent_defaults = {
                    "role": "parent",
                    "first_name": "Rodič",
                    "last_name": "Import",
                    "phone": phone,
                }
                parent, parent_created = User.objects.get_or_create(email=parent_email, defaults=parent_defaults)
                if parent_created:
                    parent.set_password("parent123")
                    parent.save(update_fields=["password"])
                    created_parents += 1
                elif phone and parent.phone != phone:
                    parent.phone = phone
                    parent.save(update_fields=["phone"])

                first_name, last_name = self._split_child_name(raw_name)
                vs_int = int(vs_raw)

                child = Child.objects.filter(variable_symbol=vs_raw).first()
                if child is None:
                    child_defaults = {
                        "parent": parent,
                        "variable_symbol": vs_raw,
                        "first_name": first_name,
                        "last_name": last_name,
                        "phone": "",
                    }
                    if year:
                        birth_number = self._build_unique_birth_number(year, vs_int, first_name)
                        if birth_number:
                            child_defaults["birth_number"] = birth_number
                        else:
                            child_defaults["passport_number"] = f"IMP-{vs_raw}"
                    else:
                        child_defaults["passport_number"] = f"IMP-{vs_raw}"

                    child = Child.objects.create(**child_defaults)
                    created_children += 1
                else:
                    updates = []
                    if child.first_name != first_name:
                        child.first_name = first_name
                        updates.append("first_name")
                    if child.last_name != last_name:
                        child.last_name = last_name
                        updates.append("last_name")
                    if child.parent_id != parent.id:
                        child.parent = parent
                        updates.append("parent")
                    if not child.birth_number and not child.passport_number:
                        if year:
                            birth_number = self._build_unique_birth_number(year, vs_int, first_name, current_child_id=child.id)
                            if birth_number:
                                child.birth_number = birth_number
                                updates.append("birth_number")
                            else:
                                child.passport_number = f"IMP-{vs_raw}"
                                updates.append("passport_number")
                        else:
                            child.passport_number = f"IMP-{vs_raw}"
                            updates.append("passport_number")
                    if updates:
                        child.save(update_fields=updates)
                        updated_children += 1

                billing_start_month = self._monthly_start(group.start_date) if group.start_date else date.today().replace(day=1)
                _, membership_created = Membership.objects.get_or_create(
                    child=child,
                    group=group,
                    defaults={
                        "active": True,
                        "billing_start_month": billing_start_month,
                    },
                )
                if membership_created:
                    created_memberships += 1

        self.stdout.write(self.style.SUCCESS("Import dokončen."))
        self.stdout.write(f"Vytvořené skupiny: {created_groups}")
        self.stdout.write(f"Vytvoření rodiče: {created_parents}")
        self.stdout.write(f"Vytvořené děti: {created_children}")
        self.stdout.write(f"Aktualizované děti: {updated_children}")
        self.stdout.write(f"Vytvořená členství: {created_memberships}")
        self.stdout.write(f"Přeskočené řádky: {skipped_rows}")
