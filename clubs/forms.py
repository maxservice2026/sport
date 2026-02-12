from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth import authenticate
from django.conf import settings
from django.db import transaction
from datetime import date
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from users.models import User
from .models import Group, AttendanceOption, Child, Membership, ReceivedPayment, ClubDocument, ChildConsent
from .pricing import group_month_starts, normalize_start_month

BIRTH_NUMBER_RE = re.compile(r'^\d{6}/\d{3,4}$')
NAME_RE = re.compile(r"^[A-Za-zÁČĎÉĚÍŇÓŘŠŤÚŮÝŽáčďéěíňóřšťúůýžÄÖÜäöüß' -]+$")
CITY_RE = re.compile(r"^[A-Za-zÁČĎÉĚÍŇÓŘŠŤÚŮÝŽáčďéěíňóřšťúůýžÄÖÜäöüß' .-]+$")
STREET_RE = re.compile(r"^[0-9A-Za-zÁČĎÉĚÍŇÓŘŠŤÚŮÝŽáčďéěíňóřšťúůýžÄÖÜäöüß/.,' -]+$")
COMMON_DIMINUTIVES = {
    'honzík', 'honza', 'péťa', 'peťa', 'anička', 'kája', 'kájík', 'míša',
    'dáda', 'venca', 'šťepa', 'luky', 'tomík', 'tomášek', 'maruška',
}
TRAINING_DAY_CHOICES = [
    ('Po', 'Pondělí'),
    ('Út', 'Úterý'),
    ('St', 'Středa'),
    ('Čt', 'Čtvrtek'),
    ('Pá', 'Pátek'),
    ('So', 'Sobota'),
    ('Ne', 'Neděle'),
]


def _normalize_spaces(value):
    return re.sub(r'\s+', ' ', (value or '').strip())


def _clean_person_name(value, field_label):
    value = _normalize_spaces(value)
    if not value:
        raise ValidationError(f'{field_label} je povinné.')
    if not NAME_RE.match(value):
        raise ValidationError(f'{field_label} obsahuje nepovolené znaky.')
    if len(value) < 2:
        raise ValidationError(f'{field_label} je příliš krátké.')
    parts = [part for part in value.split(' ') if part]
    for part in parts:
        if not part[0].isupper():
            raise ValidationError(f'{field_label} musí začínat velkým písmenem.')
        if part.lower() in COMMON_DIMINUTIVES:
            raise ValidationError(f'{field_label} vypadá jako zdrobnělina. Použijte prosím úřední tvar.')
    return value


def _normalize_cz_phone(value, required=True):
    raw = (value or '').strip()
    if not raw:
        if required:
            raise ValidationError('Telefon je povinný.')
        return ''
    normalized = raw.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    if normalized.startswith('00'):
        normalized = '+' + normalized[2:]
    if normalized.startswith('+420'):
        normalized = normalized[4:]
    elif normalized.startswith('420') and len(normalized) > 9:
        normalized = normalized[3:]
    if not normalized.isdigit():
        raise ValidationError('Telefon musí obsahovat pouze číslice.')
    if len(normalized) != 9:
        raise ValidationError('Telefon musí mít 9 číslic (CZ formát).')
    return normalized


def _clean_city_name(value):
    value = _normalize_spaces(value)
    if not value:
        raise ValidationError('Město je povinné.')
    if not CITY_RE.match(value):
        raise ValidationError('Město obsahuje nepovolené znaky.')
    return value


def _clean_street(value):
    value = _normalize_spaces(value)
    if not value:
        raise ValidationError('Ulice je povinná.')
    if len(value) < 3:
        raise ValidationError('Ulice je příliš krátká.')
    if not STREET_RE.match(value):
        raise ValidationError('Ulice obsahuje nepovolené znaky.')
    return value


def _validate_birth_number(value):
    val = (value or '').strip()
    if not val:
        return val
    if not BIRTH_NUMBER_RE.match(val):
        raise ValidationError('Rodné číslo musí být ve formátu 123456/7890.')

    base, ext = val.split('/')
    yy = int(base[0:2])
    month = int(base[2:4])
    day = int(base[4:6])

    if month > 70:
        month -= 70
    elif month > 50:
        month -= 50
    elif month > 20:
        month -= 20

    if month < 1 or month > 12:
        raise ValidationError('Rodné číslo obsahuje neplatný měsíc.')

    current_year_2 = date.today().year % 100
    if len(ext) == 3:
        year = 1900 + yy
    else:
        year = 2000 + yy if yy <= current_year_2 else 1900 + yy
        full_number = int(base + ext)
        if full_number % 11 != 0:
            raise ValidationError('Rodné číslo neprošlo kontrolou dělitelnosti 11.')

    try:
        born = date(year, month, day)
    except ValueError:
        raise ValidationError('Rodné číslo obsahuje neplatné datum.')
    if born > date.today():
        raise ValidationError('Rodné číslo obsahuje datum v budoucnosti.')

    return val


def _validate_birth_number_online(value):
    """
    Optional online validation hook.
    Set BIRTH_NUMBER_VALIDATION_URL in settings/env.
    URL can contain "{birth_number}" placeholder or accept "birth_number" query param.
    Expects JSON with one of keys: valid / is_valid / status.
    """
    endpoint = (getattr(settings, 'BIRTH_NUMBER_VALIDATION_URL', '') or '').strip()
    if not endpoint:
        return

    if '{birth_number}' in endpoint:
        url = endpoint.format(birth_number=value)
    else:
        joiner = '&' if '?' in endpoint else '?'
        url = f"{endpoint}{joiner}{urlencode({'birth_number': value})}"

    req = Request(
        url,
        headers={
            'User-Agent': 'SK-Mnisecko-Registration/1.0',
            'Accept': 'application/json',
        },
    )

    try:
        with urlopen(req, timeout=4) as resp:
            payload_raw = resp.read().decode('utf-8', errors='ignore')
    except Exception:
        # Fail-open: registration should not be blocked by temporary network outage.
        return

    try:
        payload = json.loads(payload_raw)
    except Exception:
        return

    valid = payload.get('valid')
    if valid is None:
        valid = payload.get('is_valid')
    if valid is None:
        status = str(payload.get('status', '')).strip().lower()
        if status:
            valid = status in {'ok', 'valid', 'true', '1', 'yes'}

    if valid is False:
        msg = payload.get('message') or payload.get('error') or 'Rodné číslo neprošlo online kontrolou.'
        raise ValidationError(msg)


class GroupAdminForm(forms.ModelForm):
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Začátek skupiny',
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Konec skupiny',
    )
    training_days = forms.MultipleChoiceField(
        choices=TRAINING_DAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='Dny tréninků',
    )
    trainers = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(role='trainer').order_by('last_name', 'first_name', 'email'),
        required=False,
        label='Trenéři',
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Group
        fields = [
            'sport',
            'name',
            'start_date',
            'end_date',
            'training_days',
            'registration_state',
            'max_members',
            'allow_combined_registration',
            'trainers',
        ]
        widgets = {
            'registration_state': forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['trainers'].queryset = User.objects.filter(role='trainer').order_by('last_name', 'first_name', 'email')
        if self.instance and self.instance.pk:
            self.fields['training_days'].initial = self.instance.training_days
            self.fields['trainers'].initial = self.instance.trainers.all()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.training_days = self.cleaned_data.get('training_days', [])
        # Validate date range
        start_date = self.cleaned_data.get('start_date')
        end_date = self.cleaned_data.get('end_date')
        if start_date and end_date and end_date < start_date:
            raise ValidationError('Konec skupiny nemůže být před začátkem.')
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def save_m2m(self):
        trainers = self.cleaned_data.get('trainers')
        if trainers is not None:
            self.instance.trainers.set(trainers)
        super().save_m2m()


class AttendanceOptionForm(forms.ModelForm):
    class Meta:
        model = AttendanceOption
        fields = ['name', 'frequency_per_week', 'price_czk']

    def clean(self):
        cleaned = super().clean()
        name = cleaned.get('name')
        frequency = cleaned.get('frequency_per_week')
        price = cleaned.get('price_czk')
        if any([name, frequency, price]) and not all([name, frequency, price]):
            raise ValidationError('Vyplňte název, frekvenci i cenu, nebo řádek nechte úplně prázdný.')
        return cleaned


class AdminMembershipAddForm(forms.Form):
    group = forms.ModelChoiceField(queryset=Group.objects.all(), label='Skupina')
    attendance_option = forms.ModelChoiceField(
        queryset=AttendanceOption.objects.none(),
        required=False,
        label='Docházková varianta'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        group_id = None
        if 'group' in self.data:
            try:
                group_id = int(self.data.get('group'))
            except (ValueError, TypeError):
                group_id = None
        if group_id:
            self.fields['attendance_option'].queryset = AttendanceOption.objects.filter(group_id=group_id)


class RegistrationForm(forms.Form):
    group = forms.ModelChoiceField(queryset=Group.objects.select_related('sport'), label='Skupina')
    attendance_option = forms.ModelChoiceField(
        queryset=AttendanceOption.objects.none(),
        required=False,
        label='Docházková varianta'
    )
    start_month = forms.ChoiceField(
        choices=[],
        required=False,
        label='Začátek členství od',
    )
    extra_memberships = forms.CharField(required=False, widget=forms.HiddenInput)

    birth_number = forms.CharField(required=False, label='Rodné číslo (včetně lomítka)')
    passport_number = forms.CharField(required=False, label='Číslo pasu')

    child_first_name = forms.CharField(label='Jméno dítěte')
    child_last_name = forms.CharField(label='Příjmení dítěte')
    child_phone = forms.CharField(required=False, label='Telefon dítěte')

    parent_first_name = forms.CharField(label='Jméno rodiče')
    parent_last_name = forms.CharField(label='Příjmení rodiče')
    parent_email = forms.EmailField(label='Email')
    parent_phone = forms.CharField(label='Telefon rodiče')
    parent_street = forms.CharField(label='Ulice')
    parent_city = forms.CharField(label='Město')
    parent_zip = forms.CharField(label='PSČ')

    consent_vop = forms.BooleanField(required=True, label='Souhlasím s VOP')
    consent_gdpr = forms.BooleanField(required=True, label='Souhlasím se zpracováním osobních údajů (GDPR)')
    consent_health = forms.BooleanField(required=True, label='Potvrzuji vhodný zdravotní stav dítěte pro sportovní činnost')

    password1 = forms.CharField(label='Heslo', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Heslo znovu', widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        self._current_parent = kwargs.pop('parent_user', None)
        if not (self._current_parent and getattr(self._current_parent, 'is_authenticated', False) and self._current_parent.role == 'parent'):
            self._current_parent = None
        super().__init__(*args, **kwargs)
        self._existing_parent = self._current_parent
        self._existing_child = None
        self._membership_payload = []
        self.is_parent_registration = bool(self._current_parent)
        self.fields['group'].queryset = (
            Group.objects
            .select_related('sport')
            .exclude(registration_state=Group.REG_DISABLED)
            .order_by('sport__name', 'name')
        )
        self.fields['group'].label_from_instance = self._group_label
        self.fields['start_month'].choices = [('', '— dle aktuálního měsíce —')]
        self.fields['child_first_name'].help_text = 'Použijte oficiální tvar jména (bez zdrobnělin).'
        self.fields['child_last_name'].help_text = 'Použijte oficiální tvar příjmení s diakritikou dle dokladů.'
        self.fields['parent_phone'].help_text = 'Telefon ukládáme jednotně v CZ formátu (9 číslic).'
        self.fields['parent_street'].help_text = 'Začněte psát ulici a číslo domu, nabídneme online adresy.'

        if 'group' in self.data:
            try:
                group_id = int(self.data.get('group'))
                self.fields['attendance_option'].queryset = AttendanceOption.objects.filter(group_id=group_id)
                group = Group.objects.filter(id=group_id).first()
                if group:
                    month_choices = [
                        (m.strftime('%Y-%m'), m.strftime('%m/%Y'))
                        for m in group_month_starts(group)
                        if m >= date.today().replace(day=1)
                    ]
                    self.fields['start_month'].choices = [('', '— dle aktuálního měsíce —')] + month_choices
            except (ValueError, TypeError):
                self.fields['attendance_option'].queryset = AttendanceOption.objects.none()
                self.fields['start_month'].choices = [('', '— nejdřív vyberte skupinu —')]
        else:
            self.fields['start_month'].choices = [('', '— nejdřív vyberte skupinu —')]

        if self.is_parent_registration:
            for field_name in [
                'parent_first_name',
                'parent_last_name',
                'parent_email',
                'parent_phone',
                'parent_street',
                'parent_city',
                'parent_zip',
                'password1',
                'password2',
            ]:
                self.fields[field_name].required = False
            self.fields['parent_first_name'].initial = self._current_parent.first_name
            self.fields['parent_last_name'].initial = self._current_parent.last_name
            self.fields['parent_email'].initial = self._current_parent.email
            self.fields['parent_phone'].initial = self._current_parent.phone
            self.fields['parent_street'].initial = self._current_parent.street
            self.fields['parent_city'].initial = self._current_parent.city
            self.fields['parent_zip'].initial = self._current_parent.zip_code

    @staticmethod
    def _group_label(group):
        label = f"{group.sport.name} - {group.name}"
        if group.max_members:
            slots = group.free_slots
            label += f" ({slots} volná místa)"
        if group.registration_state == Group.REG_FULL:
            label += " [OBSAZENO]"
        return label

    def clean(self):
        cleaned = super().clean()
        birth_number = (cleaned.get('birth_number') or '').strip()
        passport_number = (cleaned.get('passport_number') or '').strip()
        password1 = cleaned.get('password1')
        password2 = cleaned.get('password2')
        parent_email = cleaned.get('parent_email')
        group = cleaned.get('group')

        def _set_cleaned(field_name, cleaner, *args, **kwargs):
            try:
                cleaned[field_name] = cleaner(cleaned.get(field_name), *args, **kwargs)
            except ValidationError as exc:
                self.add_error(field_name, exc)

        if self.is_parent_registration:
            cleaned['parent_first_name'] = self._current_parent.first_name
            cleaned['parent_last_name'] = self._current_parent.last_name
            cleaned['parent_email'] = self._current_parent.email
            cleaned['parent_phone'] = self._current_parent.phone
            cleaned['parent_street'] = self._current_parent.street
            cleaned['parent_city'] = self._current_parent.city
            cleaned['parent_zip'] = self._current_parent.zip_code
            _set_cleaned('child_first_name', _clean_person_name, 'Jméno dítěte')
            _set_cleaned('child_last_name', _clean_person_name, 'Příjmení dítěte')
            _set_cleaned('child_phone', _normalize_cz_phone, required=False)
        else:
            if password1 and password2 and password1 != password2:
                self.add_error('password2', 'Hesla se neshodují.')

            parent = None
            if parent_email:
                parent = User.objects.filter(email=parent_email).first()
                if parent:
                    if parent.role != 'parent':
                        self.add_error('parent_email', 'Tento email patří jinému typu uživatele.')
                    else:
                        if not password1:
                            self.add_error('password1', 'Zadejte heslo k existujícímu rodičovskému účtu.')
                        else:
                            user = authenticate(username=parent_email, password=password1)
                            if not user:
                                self.add_error('password1', 'Nesprávné heslo pro existující rodičovský účet.')
                            else:
                                self._existing_parent = parent

            _set_cleaned('child_first_name', _clean_person_name, 'Jméno dítěte')
            _set_cleaned('child_last_name', _clean_person_name, 'Příjmení dítěte')
            _set_cleaned('child_phone', _normalize_cz_phone, required=False)
            _set_cleaned('parent_first_name', _clean_person_name, 'Jméno rodiče')
            _set_cleaned('parent_last_name', _clean_person_name, 'Příjmení rodiče')
            _set_cleaned('parent_phone', _normalize_cz_phone, required=True)
            _set_cleaned('parent_street', _clean_street)
            _set_cleaned('parent_city', _clean_city_name)
            zip_code = re.sub(r'\s+', '', (cleaned.get('parent_zip') or ''))
            if not re.match(r'^\d{5}$', zip_code):
                self.add_error('parent_zip', 'PSČ musí mít 5 číslic.')
            cleaned['parent_zip'] = zip_code

        if not birth_number and not passport_number:
            self.add_error('birth_number', 'Zadejte rodné číslo, nebo vyplňte číslo pasu u cizince.')
        if birth_number:
            try:
                birth_number = _validate_birth_number(birth_number)
                _validate_birth_number_online(birth_number)
            except ValidationError:
                self.add_error('birth_number', 'POZOR, zadáváte neplatné rodné číslo.')
        cleaned['birth_number'] = birth_number
        cleaned['passport_number'] = passport_number

        memberships_payload = []
        attendance_option = cleaned.get('attendance_option')
        start_month_raw = cleaned.get('start_month')
        if group:
            memberships_payload.append({
                'group': group,
                'attendance_option': attendance_option,
                'start_month_raw': start_month_raw,
            })

        extra_raw = (cleaned.get('extra_memberships') or '').strip()
        if extra_raw:
            try:
                parsed = json.loads(extra_raw)
            except Exception:
                parsed = []
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    try:
                        extra_group = Group.objects.get(id=int(item.get('group_id')))
                    except Exception:
                        continue
                    option_obj = None
                    option_raw = item.get('attendance_option_id')
                    if option_raw:
                        try:
                            option_obj = AttendanceOption.objects.get(id=int(option_raw), group=extra_group)
                        except Exception:
                            option_obj = None
                    memberships_payload.append({
                        'group': extra_group,
                        'attendance_option': option_obj,
                        'start_month_raw': (item.get('start_month') or '').strip(),
                    })

        unique_groups = {}
        for payload in memberships_payload:
            unique_groups[payload['group'].id] = payload
        memberships_payload = list(unique_groups.values())
        if not memberships_payload:
            self.add_error('group', 'Vyberte alespoň jednu skupinu.')

        for payload in memberships_payload:
            payload_group = payload['group']
            payload_option = payload.get('attendance_option')
            if payload_group.registration_state != Group.REG_ENABLED:
                if payload_group.registration_state == Group.REG_FULL:
                    self.add_error('group', f"Skupina {payload_group} je obsazená.")
                else:
                    self.add_error('group', f"Skupina {payload_group} není otevřená pro registraci.")
            if payload_group.max_members and payload_group.active_members_count >= payload_group.max_members:
                self.add_error('group', f"Skupina {payload_group} je naplněná.")

            has_options = AttendanceOption.objects.filter(group=payload_group).exists()
            if has_options and not payload_option:
                self.add_error('attendance_option', f"Vyberte docházkovou variantu pro skupinu {payload_group}.")
            if payload_option and payload_option.group_id != payload_group.id:
                self.add_error('attendance_option', f"Varianta nepatří do skupiny {payload_group}.")

        if len(memberships_payload) > 1:
            for payload in memberships_payload:
                if not payload['group'].allow_combined_registration:
                    self.add_error('group', f"Skupina {payload['group']} nepovoluje kombinaci s další skupinou.")
                    break

        for payload in memberships_payload:
            payload_group = payload['group']
            payload_start_raw = payload.get('start_month_raw')
            start_month_date = None
            allowed_months = [m for m in group_month_starts(payload_group) if m >= date.today().replace(day=1)]
            allowed_values = {m.strftime('%Y-%m') for m in allowed_months}
            if payload_start_raw:
                if payload_start_raw not in allowed_values:
                    self.add_error('start_month', f"Neplatný měsíc pro skupinu {payload_group}.")
                else:
                    start_month_date = date.fromisoformat(f"{payload_start_raw}-01")
            else:
                start_month_date = normalize_start_month(payload_group, fallback_date=date.today())
            payload['start_month_date'] = start_month_date

        existing_child = None
        if birth_number:
            existing_child = Child.objects.filter(birth_number=birth_number).first()
        if not existing_child and passport_number:
            existing_child = Child.objects.filter(passport_number=passport_number).first()

        if existing_child:
            self._existing_child = existing_child
            if self._existing_parent and existing_child.parent_id != self._existing_parent.id:
                self.add_error('parent_email', 'Toto dítě je již registrováno pod jiným rodičem.')
            elif not self._existing_parent:
                self.add_error('parent_email', 'Toto dítě je již registrováno. Použijte email rodiče, pod kterým je dítě vedeno, nebo kontaktujte administrátora.')
            for payload in memberships_payload:
                if Membership.objects.filter(child=existing_child, group=payload['group']).exists():
                    self.add_error('group', f"Dítě je již ve skupině {payload['group']}.")

        cleaned['membership_payload'] = memberships_payload
        self._membership_payload = memberships_payload

        return cleaned

    def save(self):
        data = self.cleaned_data
        with transaction.atomic():
            parent = self._existing_parent
            created_parent = False
            if not parent:
                parent = User.objects.create_user(
                    email=data['parent_email'],
                    password=data['password1'],
                    role='parent',
                    first_name=data['parent_first_name'],
                    last_name=data['parent_last_name'],
                    phone=data['parent_phone'],
                    street=data['parent_street'],
                    city=data['parent_city'],
                    zip_code=data['parent_zip'],
                )
                created_parent = True
            else:
                if not self.is_parent_registration:
                    # update parent data from form (keeps info current)
                    parent.first_name = data['parent_first_name']
                    parent.last_name = data['parent_last_name']
                    parent.phone = data['parent_phone']
                    parent.street = data['parent_street']
                    parent.city = data['parent_city']
                    parent.zip_code = data['parent_zip']
                    parent.save()

            child = self._existing_child
            created_child = False
            if not child:
                child = Child.objects.create(
                    parent=parent,
                    first_name=data['child_first_name'],
                    last_name=data['child_last_name'],
                    birth_number=data['birth_number'] or None,
                    passport_number=data['passport_number'] or None,
                    phone=data['child_phone'],
                )
                created_child = True

            created_count = 0
            first_membership = None
            for payload in self._membership_payload:
                membership, membership_created = Membership.objects.get_or_create(
                    child=child,
                    group=payload['group'],
                    defaults={
                        'attendance_option': payload.get('attendance_option'),
                        'billing_start_month': payload.get('start_month_date'),
                    },
                )
                if not first_membership:
                    first_membership = membership
                if not membership_created:
                    needs_save = False
                    option_obj = payload.get('attendance_option')
                    if option_obj and membership.attendance_option_id != option_obj.id:
                        membership.attendance_option = option_obj
                        needs_save = True
                    if payload.get('start_month_date') and membership.billing_start_month != payload['start_month_date']:
                        membership.billing_start_month = payload['start_month_date']
                        needs_save = True
                    if not membership.active:
                        membership.active = True
                        needs_save = True
                    if needs_save:
                        membership.save()
                else:
                    created_count += 1

            ChildConsent.objects.create(
                child=child,
                parent=parent,
                consent_vop=bool(data.get('consent_vop')),
                consent_gdpr=bool(data.get('consent_gdpr')),
                consent_health=bool(data.get('consent_health')),
                source=ChildConsent.SOURCE_REGISTRATION,
            )

        membership_created = created_count > 0
        return parent, child, first_membership, created_parent, created_child, membership_created


class ChildEditForm(forms.ModelForm):
    class Meta:
        model = Child
        fields = ['first_name', 'last_name', 'birth_number', 'passport_number', 'phone']

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('birth_number') and not cleaned.get('passport_number'):
            raise ValidationError('Je potřeba vyplnit rodné číslo nebo číslo pasu.')
        return cleaned


class ReceivedPaymentForm(forms.ModelForm):
    received_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Datum přijetí',
    )

    class Meta:
        model = ReceivedPayment
        fields = ['received_date', 'variable_symbol', 'amount_czk', 'sender_name', 'note']
        labels = {
            'variable_symbol': 'Variabilní symbol',
            'amount_czk': 'Částka (Kč)',
            'sender_name': 'Odesílatel',
            'note': 'Poznámka',
        }


class ClubDocumentForm(forms.ModelForm):
    class Meta:
        model = ClubDocument
        fields = ['title', 'file']
        labels = {
            'title': 'Název dokumentu',
            'file': 'Soubor (PDF/JPG/PNG)',
        }


class DataCompletionLookupForm(forms.Form):
    last_name = forms.CharField(required=False, label='Příjmení dítěte')
    first_name = forms.CharField(required=False, label='Jméno dítěte')
    variable_symbol = forms.CharField(required=False, label='VS dítěte')


class DataCompletionUpdateForm(forms.Form):
    parent_email = forms.EmailField(label='E-mail rodiče')
    parent_phone = forms.CharField(label='Telefon rodiče')
    parent_street = forms.CharField(label='Ulice a číslo domu')
    parent_city = forms.CharField(label='Město')
    parent_zip = forms.CharField(label='PSČ')
    child_birth_number = forms.CharField(required=False, label='Rodné číslo')
    child_passport_number = forms.CharField(required=False, label='Číslo pasu')
    consent_vop = forms.BooleanField(required=True, label='Souhlas VOP')
    consent_gdpr = forms.BooleanField(required=True, label='Souhlas GDPR')
    consent_health = forms.BooleanField(required=True, label='Souhlas zdravotní stav')

    def clean(self):
        cleaned = super().clean()
        birth_number = (cleaned.get('child_birth_number') or '').strip()
        passport_number = (cleaned.get('child_passport_number') or '').strip()
        if not birth_number and not passport_number:
            raise ValidationError('Vyplňte rodné číslo nebo číslo pasu.')
        if birth_number and not BIRTH_NUMBER_RE.match(birth_number):
            self.add_error('child_birth_number', 'Rodné číslo musí být ve formátu 123456/7890.')
        return cleaned
