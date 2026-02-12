from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth import authenticate
from django.db import transaction
from datetime import date
import re

from users.models import User
from .models import Sport, Group, AttendanceOption, Child, Membership, ReceivedPayment
from .pricing import group_month_starts, normalize_start_month

BIRTH_NUMBER_RE = re.compile(r'^\d{6}/\d{3,4}$')
TRAINING_DAY_CHOICES = [
    ('Po', 'Pondělí'),
    ('Út', 'Úterý'),
    ('St', 'Středa'),
    ('Čt', 'Čtvrtek'),
    ('Pá', 'Pátek'),
    ('So', 'Sobota'),
    ('Ne', 'Neděle'),
]


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
        queryset=User.objects.filter(role='trainer'),
        required=False,
        label='Trenéři',
    )

    class Meta:
        model = Group
        fields = ['sport', 'name', 'start_date', 'end_date', 'training_days', 'trainers']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
    sport = forms.ModelChoiceField(queryset=Sport.objects.all(), label='Sport')
    group = forms.ModelChoiceField(queryset=Group.objects.select_related('sport'), label='Skupina')
    attendance_option = forms.ModelChoiceField(
        queryset=AttendanceOption.objects.none(),
        required=False,
        label='Docházková varianta'
    )
    start_month = forms.ChoiceField(
        choices=[],
        required=False,
        label='Začátek docházky od měsíce',
    )

    id_type = forms.ChoiceField(
        choices=[('birth_number', 'Rodné číslo'), ('passport', 'Pas')],
        widget=forms.RadioSelect,
        label='Identifikace dítěte'
    )
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

    password1 = forms.CharField(label='Heslo', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Heslo znovu', widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._existing_parent = None
        self._existing_child = None
        self.fields['start_month'].choices = [('', '— dle aktuálního měsíce —')]

        if 'group' in self.data:
            try:
                group_id = int(self.data.get('group'))
                self.fields['attendance_option'].queryset = AttendanceOption.objects.filter(group_id=group_id)
                group = Group.objects.filter(id=group_id).first()
                if group:
                    month_choices = [
                        (m.strftime('%Y-%m'), m.strftime('%m/%Y'))
                        for m in group_month_starts(group)
                    ]
                    self.fields['start_month'].choices = [('', '— dle aktuálního měsíce —')] + month_choices
            except (ValueError, TypeError):
                self.fields['attendance_option'].queryset = AttendanceOption.objects.none()
                self.fields['start_month'].choices = [('', '— nejdřív vyberte skupinu —')]
        else:
            self.fields['start_month'].choices = [('', '— nejdřív vyberte skupinu —')]

    def clean(self):
        cleaned = super().clean()
        id_type = cleaned.get('id_type')
        birth_number = cleaned.get('birth_number')
        passport_number = cleaned.get('passport_number')
        password1 = cleaned.get('password1')
        password2 = cleaned.get('password2')
        parent_email = cleaned.get('parent_email')
        group = cleaned.get('group')

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

        if id_type == 'birth_number':
            if not birth_number:
                self.add_error('birth_number', 'Zadejte rodné číslo.')
            elif not BIRTH_NUMBER_RE.match(birth_number):
                self.add_error('birth_number', 'Rodné číslo musí být ve formátu 123456/7890.')
        else:
            if not passport_number:
                self.add_error('passport_number', 'Zadejte číslo pasu.')

        sport = cleaned.get('sport')
        if group and sport and group.sport_id != sport.id:
            self.add_error('group', 'Vybraná skupina nepatří do zvoleného sportu.')

        attendance_option = cleaned.get('attendance_option')
        if attendance_option and group and attendance_option.group_id != group.id:
            self.add_error('attendance_option', 'Docházková varianta nepatří do vybrané skupiny.')

        start_month_raw = cleaned.get('start_month')
        start_month_date = None
        if group:
            allowed_months = group_month_starts(group)
            allowed_values = {m.strftime('%Y-%m') for m in allowed_months}
            if start_month_raw:
                if start_month_raw not in allowed_values:
                    self.add_error('start_month', 'Vyberte měsíc v rámci období skupiny.')
                else:
                    start_month_date = date.fromisoformat(f"{start_month_raw}-01")
            else:
                start_month_date = normalize_start_month(group, fallback_date=date.today())
        cleaned['start_month_date'] = start_month_date

        existing_child = None
        if id_type == 'birth_number' and birth_number:
            existing_child = Child.objects.filter(birth_number=birth_number).first()
        if id_type == 'passport' and passport_number:
            existing_child = Child.objects.filter(passport_number=passport_number).first()

        if existing_child:
            self._existing_child = existing_child
            if self._existing_parent and existing_child.parent_id != self._existing_parent.id:
                self.add_error('parent_email', 'Toto dítě je již registrováno pod jiným rodičem.')
            elif not self._existing_parent:
                self.add_error('parent_email', 'Toto dítě je již registrováno. Použijte email rodiče, pod kterým je dítě vedeno, nebo kontaktujte administrátora.')
            if group and Membership.objects.filter(child=existing_child, group=group).exists():
                self.add_error('group', 'Dítě je již v této skupině. Vyberte jinou skupinu.')

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

            membership, membership_created = Membership.objects.get_or_create(
                child=child,
                group=data['group'],
                defaults={
                    'attendance_option': data['attendance_option'],
                    'billing_start_month': data.get('start_month_date'),
                },
            )
            if not membership_created:
                needs_save = False
                if data.get('attendance_option') and membership.attendance_option_id != data['attendance_option'].id:
                    membership.attendance_option = data['attendance_option']
                    needs_save = True
                if data.get('start_month_date') and membership.billing_start_month != data['start_month_date']:
                    membership.billing_start_month = data['start_month_date']
                    needs_save = True
                if needs_save:
                    membership.save()

        return parent, child, membership, created_parent, created_child, membership_created


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
