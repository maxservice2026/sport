from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm, UserChangeForm
from django.core.exceptions import ValidationError
from clubs.models import Group
from .models import User


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label='Email', widget=forms.EmailInput(attrs={'autofocus': True}))


class ParentProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'phone', 'street', 'city', 'zip_code']


class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('email', 'role', 'first_name', 'last_name')


class CustomUserChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = ('email', 'role', 'first_name', 'last_name', 'phone', 'street', 'city', 'zip_code')


class TrainerCreateForm(forms.ModelForm):
    password1 = forms.CharField(label='Heslo', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Heslo znovu', widget=forms.PasswordInput)
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(),
        required=False,
        label='Skupiny',
    )

    class Meta:
        model = User
        fields = ('email', 'first_name', 'last_name')

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email=email).exists():
            raise ValidationError('Email je již registrován.')
        return email

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('password1') != cleaned.get('password2'):
            self.add_error('password2', 'Hesla se neshodují.')
        return cleaned

    def save(self, commit=True):
        user = User(
            email=self.cleaned_data['email'],
            first_name=self.cleaned_data.get('first_name', ''),
            last_name=self.cleaned_data.get('last_name', ''),
            role='trainer',
        )
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
            groups = self.cleaned_data.get('groups')
            if groups is not None:
                user.assigned_groups.set(groups)
        return user


class TrainerUpdateForm(forms.ModelForm):
    new_password1 = forms.CharField(label='Nové heslo', widget=forms.PasswordInput, required=False)
    new_password2 = forms.CharField(label='Nové heslo znovu', widget=forms.PasswordInput, required=False)
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(),
        required=False,
        label='Skupiny',
    )

    class Meta:
        model = User
        fields = ('email', 'first_name', 'last_name')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['groups'].initial = self.instance.assigned_groups.all()

    def clean_email(self):
        email = self.cleaned_data['email']
        qs = User.objects.filter(email=email).exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError('Email je již registrován.')
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('new_password1')
        p2 = cleaned.get('new_password2')
        if p1 or p2:
            if p1 != p2:
                self.add_error('new_password2', 'Hesla se neshodují.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = 'trainer'
        if self.cleaned_data.get('new_password1'):
            user.set_password(self.cleaned_data['new_password1'])
        if commit:
            user.save()
            groups = self.cleaned_data.get('groups')
            if groups is not None:
                user.assigned_groups.set(groups)
        return user
