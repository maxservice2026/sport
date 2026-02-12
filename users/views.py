from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import imaplib
import smtplib
import ssl

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.views import PasswordResetView, PasswordResetDoneView, PasswordResetConfirmView, PasswordResetCompleteView
from django.db.models import Count, Q, Sum
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse, reverse_lazy

from .forms import (
    EmailAuthenticationForm,
    SilentPasswordResetForm,
    ParentProfileForm,
    TrainerCreateForm,
    TrainerUpdateForm,
    AppSettingsForm,
)
from .utils import role_required, get_app_settings
from clubs.models import Group, Sport, Child, Membership, TrainerGroup, SaleCharge, ReceivedPayment, ChildFinanceEntry, ClubDocument
from clubs.payments import build_payment_counter, consume_matching_payment
from clubs.pricing import normalize_start_month, payable_months_count, prorated_amount, month_start
from users.models import User, EconomyExpense, EconomyRecurringExpense
from clubs.forms import ChildEditForm
from attendance.models import TrainerAttendance, Attendance


DAY_TO_WEEKDAY = {
    'Po': 0,
    'Út': 1,
    'St': 2,
    'Čt': 3,
    'Pá': 4,
    'So': 5,
    'Ne': 6,
}


class UserPasswordResetView(PasswordResetView):
    template_name = 'accounts/password_reset_form.html'
    email_template_name = 'accounts/password_reset_email.txt'
    subject_template_name = 'accounts/password_reset_subject.txt'
    form_class = SilentPasswordResetForm
    success_url = reverse_lazy('password_reset_done')


class UserPasswordResetDoneView(PasswordResetDoneView):
    template_name = 'accounts/password_reset_done.html'


class UserPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')


class UserPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = 'accounts/password_reset_complete.html'


def _add_months_safe(value, months):
    month_idx = (value.month - 1) + months
    year = value.year + (month_idx // 12)
    month = (month_idx % 12) + 1
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    month_end = next_month_start - timedelta(days=1)
    day = min(value.day, month_end.day)
    return date(year, month, day)


def _period_range(period, today=None):
    today = today or date.today()
    start_current_month = date(today.year, today.month, 1)
    if period == 'current_month':
        return start_current_month, today
    if period == 'last_month':
        last_month_end = start_current_month - timedelta(days=1)
        return date(last_month_end.year, last_month_end.month, 1), last_month_end
    return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)


def _round_czk(value):
    return Decimal(value or 0).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def _next_recurrence_date(current_date, recurrence):
    if recurrence == EconomyRecurringExpense.RECUR_WEEKLY:
        return current_date + timedelta(days=7)
    if recurrence == EconomyRecurringExpense.RECUR_14_DAYS:
        return current_date + timedelta(days=14)
    if recurrence == EconomyRecurringExpense.RECUR_MONTHLY:
        return _add_months_safe(current_date, 1)
    if recurrence == EconomyRecurringExpense.RECUR_QUARTERLY:
        return _add_months_safe(current_date, 3)
    if recurrence == EconomyRecurringExpense.RECUR_YEARLY:
        return _add_months_safe(current_date, 12)
    return current_date + timedelta(days=30)


def _generate_due_recurring_expenses(until_date):
    recurring = (
        EconomyRecurringExpense.objects
        .filter(active=True, next_run_date__lte=until_date)
        .order_by('next_run_date', 'id')
    )
    generated_count = 0
    for item in recurring:
        run_date = item.next_run_date
        while run_date and run_date <= until_date:
            if not EconomyExpense.objects.filter(recurring_source=item, expense_date=run_date).exists():
                EconomyExpense.objects.create(
                    expense_date=run_date,
                    title=item.title,
                    amount_czk=item.amount_czk,
                    note=item.note,
                    recurring_source=item,
                    created_by=item.created_by,
                )
                generated_count += 1
            run_date = _next_recurrence_date(run_date, item.recurrence)
        if run_date != item.next_run_date:
            item.next_run_date = run_date
            item.save(update_fields=['next_run_date'])
    return generated_count


def _run_payment_email_connection_test(cleaned_data):
    mode = cleaned_data.get('payment_email_mode')
    if mode == 'forward':
        email = (cleaned_data.get('payment_forward_email') or '').strip()
        if not email:
            return False, 'Pro režim přesměrování vyplňte cílový email.'
        return True, f'Režim přesměrování je nastaven na {email}.'

    imap_host = (cleaned_data.get('payment_imap_host') or '').strip()
    imap_port = cleaned_data.get('payment_imap_port')
    imap_user = (cleaned_data.get('payment_imap_user') or '').strip()
    imap_password = cleaned_data.get('payment_imap_password') or ''
    smtp_host = (cleaned_data.get('payment_smtp_host') or '').strip()
    smtp_port = cleaned_data.get('payment_smtp_port')
    smtp_user = (cleaned_data.get('payment_smtp_user') or '').strip()
    smtp_password = cleaned_data.get('payment_smtp_password') or ''

    if not all([imap_host, imap_port, imap_user, imap_password, smtp_host, smtp_port, smtp_user, smtp_password]):
        return False, 'Pro test IMAP/SMTP vyplňte všechny údaje serveru, uživatele a hesla.'

    details = []
    try:
        imap_client = imaplib.IMAP4_SSL(imap_host, int(imap_port), timeout=10)
        imap_client.login(imap_user, imap_password)
        imap_client.logout()
        details.append('IMAP: OK')
    except Exception as exc:
        return False, f'IMAP test selhal: {exc}'

    try:
        if int(smtp_port) == 465:
            smtp_client = smtplib.SMTP_SSL(smtp_host, int(smtp_port), timeout=10, context=ssl.create_default_context())
            smtp_client.ehlo()
        else:
            smtp_client = smtplib.SMTP(smtp_host, int(smtp_port), timeout=10)
            smtp_client.ehlo()
            if int(smtp_port) in (25, 587):
                smtp_client.starttls(context=ssl.create_default_context())
                smtp_client.ehlo()
        smtp_client.login(smtp_user, smtp_password)
        smtp_client.quit()
        details.append('SMTP: OK')
    except Exception as exc:
        return False, f'SMTP test selhal: {exc}'

    return True, 'Spojení v pořádku. ' + ' | '.join(details)


def _child_gender(child):
    birth_number = (child.birth_number or '').strip()
    if birth_number:
        raw = birth_number.split('/')[0]
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if len(digits) >= 4:
            month_raw = int(digits[2:4])
            if month_raw > 70 or month_raw > 50:
                return 'female'
            if month_raw > 20:
                return 'male'
            return 'female' if month_raw > 12 else 'male'

    # Fallback for children without birth number (passport records).
    name = (child.first_name or '').strip().lower()
    if name.endswith('a'):
        return 'female'
    return 'male'


def _group_training_dates_to_date(group, target_date):
    if not group.start_date or not group.end_date:
        return []
    if group.start_date > group.end_date:
        return []

    end = min(group.end_date, target_date)
    if end < group.start_date:
        return []

    allowed = {DAY_TO_WEEKDAY.get(day) for day in (group.training_days or []) if DAY_TO_WEEKDAY.get(day) is not None}
    if not allowed:
        allowed = set(range(7))

    dates = []
    cur = group.start_date
    while cur <= end:
        if cur.weekday() in allowed:
            dates.append(cur)
        cur += timedelta(days=1)
    return dates


def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')

    form = EmailAuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect(request.GET.get('next') or 'home')
        messages.error(request, 'Neplatný email nebo heslo.')

    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('login')


def home(request):
    if not request.user.is_authenticated:
        return redirect('login')

    if request.user.role == 'admin':
        return redirect('admin_dashboard')
    if request.user.role == 'trainer':
        return redirect('trainer_dashboard')
    return redirect('parent_dashboard')


@role_required('admin')
def admin_dashboard(request):
    attendance_date_raw = (request.GET.get('attendance_date') or '').strip()
    try:
        attendance_date = date.fromisoformat(attendance_date_raw) if attendance_date_raw else date.today()
    except ValueError:
        attendance_date = date.today()

    sport_count = Sport.objects.filter(groups__isnull=False).distinct().count()
    group_count = Group.objects.count()
    child_count = Child.objects.count()
    trainer_count = TrainerGroup.objects.values('trainer_id').distinct().count()

    memberships = list(
        Membership.objects
        .filter(active=True)
        .select_related('group', 'attendance_option', 'child')
        .order_by('id')
    )

    # Průměrná docházka přes všechna aktivní členství k zadanému datu.
    memberships_by_group = {}
    for membership in memberships:
        memberships_by_group.setdefault(membership.group_id, []).append(membership)

    total_expected_records = 0
    total_present_records = 0
    for group_id, group_memberships in memberships_by_group.items():
        group = group_memberships[0].group
        training_dates = _group_training_dates_to_date(group, attendance_date)
        total_training_days = len(training_dates)
        if total_training_days <= 0:
            continue

        child_ids = [m.child_id for m in group_memberships]
        present_rows = (
            Attendance.objects
            .filter(
                session__group_id=group_id,
                session__date__in=training_dates,
                child_id__in=child_ids,
                present=True,
            )
            .values('child_id')
            .annotate(cnt=Count('id'))
        )
        present_map = {row['child_id']: row['cnt'] for row in present_rows}
        for membership in group_memberships:
            present_count = present_map.get(membership.child_id, 0)
            total_expected_records += total_training_days
            total_present_records += present_count

    attendance_average_percent = int(round((total_present_records / total_expected_records) * 100)) if total_expected_records else 0

    # Očekávané příspěvky bereme z autorizovaných záloh/faktur.
    # Otevřená záloha = čekáme na úhradu, uzavřená faktura = uhrazeno.
    expected_total = (
        ChildFinanceEntry.objects
        .filter(
            direction=ChildFinanceEntry.DIR_DEBIT,
        )
        .filter(
            Q(
                event_type=ChildFinanceEntry.TYPE_PROFORMA,
                status=ChildFinanceEntry.STATUS_OPEN,
            ) |
            Q(
                event_type=ChildFinanceEntry.TYPE_INVOICE,
                status=ChildFinanceEntry.STATUS_CLOSED,
            )
        )
        .aggregate(total=Sum('amount_czk'))['total']
        or Decimal('0.00')
    )
    paid_total = (
        ChildFinanceEntry.objects
        .filter(
            direction=ChildFinanceEntry.DIR_DEBIT,
            event_type=ChildFinanceEntry.TYPE_INVOICE,
            status=ChildFinanceEntry.STATUS_CLOSED,
        )
        .aggregate(total=Sum('amount_czk'))['total']
        or Decimal('0.00')
    )

    boys_count = 0
    girls_count = 0
    for child in Child.objects.only('birth_number', 'first_name'):
        if _child_gender(child) == 'female':
            girls_count += 1
        else:
            boys_count += 1

    return render(request, 'admin/dashboard.html', {
        'sport_count': sport_count,
        'group_count': group_count,
        'child_count': child_count,
        'trainer_count': trainer_count,
        'boys_count': boys_count,
        'girls_count': girls_count,
        'attendance_date': attendance_date,
        'attendance_average_percent': attendance_average_percent,
        'contributions_expected': expected_total,
        'contributions_paid': paid_total,
        'groups_nav': Group.objects.select_related('sport').order_by('sport__name', 'name'),
    })


@role_required('admin')
def admin_trainer_list(request):
    trainers = User.objects.filter(role='trainer').order_by('last_name', 'first_name')
    return render(request, 'admin/trainers_list.html', {
        'trainers': trainers,
        'groups_nav': Group.objects.select_related('sport').order_by('sport__name', 'name'),
    })


@role_required('admin')
def admin_trainer_create(request):
    form = TrainerCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Trenér byl vytvořen.')
        return redirect('admin_trainers')
    return render(request, 'admin/trainer_form.html', {
        'form': form,
        'is_edit': False,
        'groups_nav': Group.objects.select_related('sport').order_by('sport__name', 'name'),
    })


@role_required('admin')
def admin_trainer_edit(request, user_id):
    trainer = get_object_or_404(User, id=user_id, role='trainer')
    form = TrainerUpdateForm(request.POST or None, instance=trainer)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Trenér byl uložen.')
        return redirect('admin_trainers')
    return render(request, 'admin/trainer_form.html', {
        'form': form,
        'trainer': trainer,
        'is_edit': True,
        'groups_nav': Group.objects.select_related('sport').order_by('sport__name', 'name'),
    })


@role_required('admin')
def admin_economics(request):
    period = request.GET.get('period') or request.POST.get('period') or 'current_month'
    period_choices = [
        ('current_month', 'Aktuální měsíc'),
        ('last_month', 'Minulý měsíc'),
        ('last_year', 'Minulý rok'),
    ]
    allowed_periods = {value for value, _ in period_choices}
    if period not in allowed_periods:
        period = 'current_month'

    today = date.today()
    range_start, range_end = _period_range(period, today=today)

    if request.method == 'POST':
        action = request.POST.get('action') or 'trainer_update'
        if action == 'expense_add':
            title = (request.POST.get('expense_title') or '').strip()
            amount_raw = (request.POST.get('expense_amount_czk') or '').strip().replace(',', '.')
            note = (request.POST.get('expense_note') or '').strip()
            expense_date_raw = (request.POST.get('expense_date') or '').strip()

            if not title:
                messages.error(request, 'Vyplňte název nákladové položky.')
                return redirect(f"{reverse('admin_economics')}?period={period}")
            try:
                amount = Decimal(amount_raw)
            except Exception:
                messages.error(request, 'Částka musí být číslo.')
                return redirect(f"{reverse('admin_economics')}?period={period}")
            if amount <= 0:
                messages.error(request, 'Částka musí být větší než 0.')
                return redirect(f"{reverse('admin_economics')}?period={period}")
            try:
                expense_date = date.fromisoformat(expense_date_raw) if expense_date_raw else date.today()
            except ValueError:
                messages.error(request, 'Neplatné datum nákladu.')
                return redirect(f"{reverse('admin_economics')}?period={period}")

            EconomyExpense.objects.create(
                expense_date=expense_date,
                title=title,
                amount_czk=amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                expense_type=EconomyExpense.TYPE_GENERAL,
                note=note,
                created_by=request.user,
            )
            messages.success(request, 'Nákladová položka byla vytvořena.')
            return redirect(f"{reverse('admin_economics')}?period={period}")

        if action == 'recurring_expense_add':
            title = (request.POST.get('recurring_title') or '').strip()
            amount_raw = (request.POST.get('recurring_amount_czk') or '').strip().replace(',', '.')
            note = (request.POST.get('recurring_note') or '').strip()
            start_date_raw = (request.POST.get('recurring_start_date') or '').strip()
            recurrence = (request.POST.get('recurring_recurrence') or '').strip()

            if not title:
                messages.error(request, 'Vyplňte název opakovaného nákladu.')
                return redirect(f"{reverse('admin_economics')}?period={period}")
            try:
                amount = Decimal(amount_raw)
            except Exception:
                messages.error(request, 'Částka opakovaného nákladu musí být číslo.')
                return redirect(f"{reverse('admin_economics')}?period={period}")
            if amount <= 0:
                messages.error(request, 'Částka opakovaného nákladu musí být větší než 0.')
                return redirect(f"{reverse('admin_economics')}?period={period}")
            try:
                start_date = date.fromisoformat(start_date_raw) if start_date_raw else today
            except ValueError:
                messages.error(request, 'Neplatné datum začátku opakovaného nákladu.')
                return redirect(f"{reverse('admin_economics')}?period={period}")
            allowed_recurrence = {value for value, _ in EconomyRecurringExpense.RECUR_CHOICES}
            if recurrence not in allowed_recurrence:
                messages.error(request, 'Vyberte platné opakování nákladu.')
                return redirect(f"{reverse('admin_economics')}?period={period}")

            EconomyRecurringExpense.objects.create(
                title=title,
                amount_czk=amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                note=note,
                recurrence=recurrence,
                start_date=start_date,
                next_run_date=start_date,
                active=True,
                created_by=request.user,
            )
            _generate_due_recurring_expenses(today)
            messages.success(request, 'Opakovaný náklad byl vytvořen.')
            return redirect(f"{reverse('admin_economics')}?period={period}")

        if action == 'recurring_toggle':
            recurring_id = request.POST.get('recurring_id')
            recurring_expense = get_object_or_404(EconomyRecurringExpense, id=recurring_id)
            recurring_expense.active = not recurring_expense.active
            recurring_expense.save(update_fields=['active'])
            messages.success(request, 'Stav opakovaného nákladu byl změněn.')
            return redirect(f"{reverse('admin_economics')}?period={period}")

        trainer_id = request.POST.get('trainer_id')
        trainer = get_object_or_404(User, id=trainer_id, role='trainer')
        payment_mode = request.POST.get('trainer_payment_mode')
        tax_enabled = request.POST.get('trainer_tax_15_enabled') == 'on'
        rate_raw = request.POST.get('trainer_rate_per_record', '0').strip()

        allowed_modes = {choice[0] for choice in User.PAYMENT_CHOICES}
        if payment_mode not in allowed_modes:
            messages.error(request, 'Neplatný způsob odměny.')
            return redirect(f"{reverse('admin_economics')}?period={period}")

        try:
            rate = int(rate_raw)
        except ValueError:
            messages.error(request, 'Částka za záznam musí být celé číslo.')
            return redirect(f"{reverse('admin_economics')}?period={period}")
        if rate < 0:
            messages.error(request, 'Částka za záznam nemůže být záporná.')
            return redirect(f"{reverse('admin_economics')}?period={period}")

        trainer.trainer_payment_mode = payment_mode
        trainer.trainer_tax_15_enabled = tax_enabled
        trainer.trainer_rate_per_record = rate
        trainer.save(update_fields=['trainer_payment_mode', 'trainer_tax_15_enabled', 'trainer_rate_per_record'])
        return redirect(f"{reverse('admin_economics')}?period={period}")

    generated_now = _generate_due_recurring_expenses(today)
    if generated_now:
        messages.info(request, f'Automaticky doplněné opakované náklady: {generated_now}.')

    attendance_filter = Q(
        trainer_attendance_records__present=True,
        trainer_attendance_records__session__date__gte=range_start,
        trainer_attendance_records__session__date__lte=range_end,
    )

    trainers = (
        User.objects
        .filter(role='trainer')
        .annotate(
            attendance_count=Count(
                'trainer_attendance_records',
                filter=attendance_filter,
            ),
            attendance_extra_count=Count(
                'trainer_attendance_records',
                filter=attendance_filter & Q(trainer_attendance_records__extra_access=True),
            ),
        )
        .order_by('last_name', 'first_name', 'email')
    )

    trainers = list(trainers)
    trainer_ids = [trainer.id for trainer in trainers]
    records_map = {trainer_id: [] for trainer_id in trainer_ids}
    if trainer_ids:
        records = (
            TrainerAttendance.objects
            .filter(
                trainer_id__in=trainer_ids,
                present=True,
                session__date__gte=range_start,
                session__date__lte=range_end,
            )
            .select_related('trainer', 'session__group', 'session__group__sport')
            .order_by('-session__date', '-id')
        )
        for record in records:
            group = record.session.group
            records_map[record.trainer_id].append({
                'date': record.session.date,
                'trainer_name': f"{record.trainer.first_name} {record.trainer.last_name}".strip() or record.trainer.email,
                'group_name': f"{group.sport.name} - {group.name}",
                'extra_access': record.extra_access,
            })

    rows = []
    total_records = 0
    total_gross = Decimal('0.00')
    total_tax = Decimal('0.00')
    total_net = Decimal('0.00')
    total_reimbursement = Decimal('0.00')
    total_payout = Decimal('0.00')

    service_sum_rows = (
        EconomyExpense.objects
        .filter(
            trainer__in=trainers,
            expense_type=EconomyExpense.TYPE_TRAINER_SERVICE,
            expense_date__gte=range_start,
            expense_date__lte=range_end,
        )
        .values('trainer_id')
        .annotate(total=Sum('amount_czk'))
    )
    reimbursement_sum_rows = (
        EconomyExpense.objects
        .filter(
            trainer__in=trainers,
            expense_type=EconomyExpense.TYPE_TRAINER_REIMBURSEMENT,
            expense_date__gte=range_start,
            expense_date__lte=range_end,
        )
        .values('trainer_id')
        .annotate(total=Sum('amount_czk'))
    )
    service_sum_map = {row['trainer_id']: row['total'] or Decimal('0.00') for row in service_sum_rows}
    reimbursement_sum_map = {row['trainer_id']: row['total'] or Decimal('0.00') for row in reimbursement_sum_rows}

    for trainer in trainers:
        base_gross = Decimal(trainer.trainer_rate_per_record) * Decimal(trainer.attendance_count)
        service_gross = Decimal(service_sum_map.get(trainer.id, Decimal('0.00')))
        gross = base_gross + service_gross
        if trainer.trainer_tax_15_enabled:
            tax = _round_czk(gross * Decimal('0.15'))
        else:
            tax = Decimal('0.00')
        reimbursement = Decimal(reimbursement_sum_map.get(trainer.id, Decimal('0.00')))
        net = gross - tax
        payout_total = net + reimbursement

        gross = _round_czk(gross)
        net = _round_czk(net)
        reimbursement = _round_czk(reimbursement)
        payout_total = _round_czk(payout_total)

        total_records += trainer.attendance_count
        total_gross += gross
        total_tax += tax
        total_net += net
        total_reimbursement += reimbursement
        total_payout += payout_total
        rows.append({
            'trainer': trainer,
            'attendance_count': trainer.attendance_count,
            'attendance_extra_count': trainer.attendance_extra_count,
            'base_gross': _round_czk(base_gross),
            'service_gross': _round_czk(service_gross),
            'gross': gross,
            'tax': tax,
            'net': net,
            'reimbursement': reimbursement,
            'payout_total': payout_total,
            'records': records_map.get(trainer.id, []),
        })
    expenses = list(
        EconomyExpense.objects
        .filter(expense_date__gte=range_start, expense_date__lte=range_end)
        .select_related('created_by', 'recurring_source')
        .order_by('-expense_date', '-id')
    )
    recurring_expenses = list(
        EconomyRecurringExpense.objects
        .select_related('created_by')
        .order_by('title', 'id')
    )
    total_expenses = _round_czk(sum((expense.amount_czk for expense in expenses), Decimal('0.00')))
    total_gross = _round_czk(total_gross)
    total_tax = _round_czk(total_tax)
    total_net = _round_czk(total_net)
    total_reimbursement = _round_czk(total_reimbursement)
    total_payout = _round_czk(total_payout)

    return render(request, 'admin/economics.html', {
        'rows': rows,
        'period': period,
        'period_choices': period_choices,
        'range_start': range_start,
        'range_end': range_end,
        'payment_choices': User.PAYMENT_CHOICES,
        'total_records': total_records,
        'total_gross': total_gross,
        'total_tax': total_tax,
        'total_net': total_net,
        'total_reimbursement': total_reimbursement,
        'total_payout': total_payout,
        'expenses': expenses,
        'recurring_expenses': recurring_expenses,
        'recurring_choices': EconomyRecurringExpense.RECUR_CHOICES,
        'total_expenses': total_expenses,
        'groups_nav': Group.objects.select_related('sport').order_by('sport__name', 'name'),
        'admin_wide_content': True,
    })


@role_required('admin')
def admin_settings(request):
    settings_obj = get_app_settings()
    form = AppSettingsForm(request.POST or None, instance=settings_obj)
    if request.method == 'POST':
        action = (request.POST.get('action') or 'save').strip()
        if form.is_valid():
            if action == 'test_email':
                ok, msg = _run_payment_email_connection_test(form.cleaned_data)
                if ok:
                    messages.success(request, f'Test e-mailového spojení: {msg}')
                else:
                    messages.error(request, f'Test e-mailového spojení: {msg}')
            else:
                form.save()
                messages.success(request, 'Nastavení aplikace bylo uloženo.')
                return redirect('admin_settings')
    return render(request, 'admin/settings.html', {
        'form': form,
        'groups_nav': Group.objects.select_related('sport').order_by('sport__name', 'name'),
        'admin_wide_content': True,
    })


@role_required('trainer')
def trainer_economics(request):
    period = request.GET.get('period') or request.POST.get('period') or 'current_month'
    period_choices = [
        ('current_month', 'Aktuální měsíc'),
        ('last_month', 'Minulý měsíc'),
        ('last_year', 'Minulý rok'),
    ]
    allowed_periods = {value for value, _ in period_choices}
    if period not in allowed_periods:
        period = 'current_month'

    today = date.today()
    range_start, range_end = _period_range(period, today=today)

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'trainer_expense_add':
            expense_date_raw = (request.POST.get('expense_date') or '').strip()
            title = (request.POST.get('expense_title') or '').strip()
            amount_raw = (request.POST.get('expense_amount_czk') or '').strip().replace(',', '.')
            note = (request.POST.get('expense_note') or '').strip()
            expense_type = (request.POST.get('expense_type') or '').strip()
            allowed_types = {
                EconomyExpense.TYPE_TRAINER_SERVICE,
                EconomyExpense.TYPE_TRAINER_REIMBURSEMENT,
            }
            if expense_type not in allowed_types:
                messages.error(request, 'Vyberte typ nákladu.')
                return redirect(f"{reverse('trainer_economics')}?period={period}")
            if not title:
                messages.error(request, 'Vyplňte název položky.')
                return redirect(f"{reverse('trainer_economics')}?period={period}")
            try:
                amount = Decimal(amount_raw)
            except Exception:
                messages.error(request, 'Částka musí být číslo.')
                return redirect(f"{reverse('trainer_economics')}?period={period}")
            if amount <= 0:
                messages.error(request, 'Částka musí být větší než 0.')
                return redirect(f"{reverse('trainer_economics')}?period={period}")
            try:
                expense_date = date.fromisoformat(expense_date_raw) if expense_date_raw else today
            except ValueError:
                messages.error(request, 'Neplatné datum.')
                return redirect(f"{reverse('trainer_economics')}?period={period}")

            EconomyExpense.objects.create(
                expense_date=expense_date,
                title=title,
                amount_czk=amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                expense_type=expense_type,
                trainer=request.user,
                note=note,
                created_by=request.user,
            )
            messages.success(request, 'Položka byla uložena.')
            return redirect(f"{reverse('trainer_economics')}?period={period}")

    attendance_count = TrainerAttendance.objects.filter(
        trainer=request.user,
        present=True,
        session__date__gte=range_start,
        session__date__lte=range_end,
    ).count()
    attendance_records = list(
        TrainerAttendance.objects
        .filter(
            trainer=request.user,
            present=True,
            session__date__gte=range_start,
            session__date__lte=range_end,
        )
        .select_related('session__group', 'session__group__sport')
        .order_by('-session__date', '-id')
    )

    service_total = (
        EconomyExpense.objects
        .filter(
            trainer=request.user,
            expense_type=EconomyExpense.TYPE_TRAINER_SERVICE,
            expense_date__gte=range_start,
            expense_date__lte=range_end,
        )
        .aggregate(total=Sum('amount_czk'))['total']
        or Decimal('0.00')
    )
    reimbursement_total = (
        EconomyExpense.objects
        .filter(
            trainer=request.user,
            expense_type=EconomyExpense.TYPE_TRAINER_REIMBURSEMENT,
            expense_date__gte=range_start,
            expense_date__lte=range_end,
        )
        .aggregate(total=Sum('amount_czk'))['total']
        or Decimal('0.00')
    )
    trainer_expenses = list(
        EconomyExpense.objects
        .filter(
            trainer=request.user,
            expense_type__in=[EconomyExpense.TYPE_TRAINER_SERVICE, EconomyExpense.TYPE_TRAINER_REIMBURSEMENT],
            expense_date__gte=range_start,
            expense_date__lte=range_end,
        )
        .order_by('-expense_date', '-id')
    )

    base_gross = Decimal(request.user.trainer_rate_per_record) * Decimal(attendance_count)
    gross = base_gross + service_total
    tax = _round_czk(gross * Decimal('0.15')) if request.user.trainer_tax_15_enabled else Decimal('0.00')
    net = gross - tax
    payout_total = net + reimbursement_total

    return render(request, 'trainer/economics.html', {
        'period': period,
        'period_choices': period_choices,
        'range_start': range_start,
        'range_end': range_end,
        'attendance_count': attendance_count,
        'attendance_records': attendance_records,
        'base_gross': _round_czk(base_gross),
        'service_total': _round_czk(service_total),
        'gross': _round_czk(gross),
        'tax': _round_czk(tax),
        'net': _round_czk(net),
        'reimbursement_total': _round_czk(reimbursement_total),
        'payout_total': _round_czk(payout_total),
        'trainer_expenses': trainer_expenses,
        'expense_type_service': EconomyExpense.TYPE_TRAINER_SERVICE,
        'expense_type_reimbursement': EconomyExpense.TYPE_TRAINER_REIMBURSEMENT,
    })


@role_required('trainer')
def trainer_dashboard(request):
    show_all = request.GET.get('all') == '1'
    assigned_ids = set(Group.objects.filter(trainers__id=request.user.id).values_list('id', flat=True))
    if show_all:
        groups = Group.objects.select_related('sport').order_by('sport__name', 'name')
    else:
        groups = Group.objects.filter(id__in=assigned_ids).select_related('sport').order_by('sport__name', 'name')
    return render(request, 'trainer/dashboard.html', {
        'groups': groups,
        'show_all': show_all,
        'assigned_ids': assigned_ids,
    })


@role_required('parent')
def parent_dashboard(request):
    children = list(
        Child.objects
        .filter(parent=request.user)
        .prefetch_related('memberships__group', 'memberships__attendance_option')
    )
    charges_qs = (
        SaleCharge.objects
        .filter(child__parent=request.user)
        .select_related('child')
        .order_by('created_at', 'id')
    )
    payment_counter = build_payment_counter(ReceivedPayment.objects.all().only('variable_symbol', 'amount_czk'))
    charge_paid_map = {}
    for charge in charges_qs:
        charge_paid_map[charge.id] = consume_matching_payment(
            payment_counter,
            charge.child.variable_symbol,
            charge.amount_czk,
        )

    child_charges = {}
    for charge in charges_qs:
        charge.paid = charge_paid_map.get(charge.id, False)
        child_charges.setdefault(charge.child_id, []).append(charge)

    due_totals_map = {
        row['child_id']: row['total'] or Decimal('0.00')
        for row in (
            ChildFinanceEntry.objects
            .filter(
                child__parent=request.user,
                direction=ChildFinanceEntry.DIR_DEBIT,
                status=ChildFinanceEntry.STATUS_OPEN,
            )
            .values('child_id')
            .annotate(total=Sum('amount_czk'))
        )
    }

    for child in children:
        child.charge_rows = child_charges.get(child.id, [])
        child.finance_rows = list(
            ChildFinanceEntry.objects
            .filter(child=child)
            .select_related('membership', 'membership__group')
            .order_by('-occurred_on', '-id')[:30]
        )
        for row in child.finance_rows:
            if row.event_type in (ChildFinanceEntry.TYPE_PROFORMA, ChildFinanceEntry.TYPE_INVOICE):
                row.display_title = 'Členství'
            else:
                row.display_title = row.title

            if row.direction == ChildFinanceEntry.DIR_DEBIT and row.status == ChildFinanceEntry.STATUS_OPEN:
                row.parent_status_label = 'Nezaplaceno'
            elif row.status == ChildFinanceEntry.STATUS_CLOSED and row.direction == ChildFinanceEntry.DIR_DEBIT:
                row.parent_status_label = 'Uhrazeno'
            elif row.status == ChildFinanceEntry.STATUS_CANCELLED:
                row.parent_status_label = 'Storno'
            else:
                row.parent_status_label = row.get_status_display()
            row.can_qr = (
                row.direction == ChildFinanceEntry.DIR_DEBIT
                and row.status == ChildFinanceEntry.STATUS_OPEN
                and row.amount_czk > 0
            )
        child.open_debit_rows = [
            row for row in child.finance_rows
            if row.direction == ChildFinanceEntry.DIR_DEBIT
            and row.status == ChildFinanceEntry.STATUS_OPEN
            and row.amount_czk > 0
        ]

        child.attendance_preview = list(
            Attendance.objects
            .filter(child=child)
            .select_related('session__group', 'session__group__sport')
            .order_by('-session__date', '-id')[:3]
        )
        child.active_memberships = [m for m in child.memberships.all() if m.active]
        child.ended_memberships = [m for m in child.memberships.all() if not m.active]
        child.due_total = due_totals_map.get(child.id, Decimal('0.00'))

    proforma_documents = list(
        ChildFinanceEntry.objects
        .filter(
            child__parent=request.user,
            event_type=ChildFinanceEntry.TYPE_PROFORMA,
        )
        .select_related('child', 'membership', 'membership__group')
        .order_by('-occurred_on', '-id')
    )

    return render(request, 'parent/dashboard.html', {
        'children': children,
        'proforma_documents': proforma_documents,
        'documents': ClubDocument.objects.order_by('-uploaded_at', '-id')[:30],
    })


@role_required('parent')
def parent_profile(request):
    form = ParentProfileForm(request.POST or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Profil byl uložen.')
        return redirect('parent_dashboard')
    return render(request, 'parent/profile.html', {'form': form})


@role_required('parent')
def parent_child_detail(request, child_id):
    child = get_object_or_404(Child, id=child_id, parent=request.user)
    form = ChildEditForm(request.POST or None, instance=child)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Údaje dítěte byly uloženy.')
        return redirect('parent_dashboard')
    memberships = Membership.objects.filter(child=child).select_related('group', 'attendance_option')
    sale_charges = list(
        SaleCharge.objects
        .filter(child=child)
        .order_by('created_at', 'id')
    )
    payment_counter = build_payment_counter(ReceivedPayment.objects.all().only('variable_symbol', 'amount_czk'))
    for charge in sale_charges:
        charge.paid = consume_matching_payment(
            payment_counter,
            child.variable_symbol,
            charge.amount_czk,
        )
    selected_entry = None
    qr_entry_id = (request.GET.get('qr_entry') or '').strip()
    if qr_entry_id.isdigit():
        selected_entry = (
            ChildFinanceEntry.objects
            .filter(
                id=qr_entry_id,
                child=child,
                direction=ChildFinanceEntry.DIR_DEBIT,
                status=ChildFinanceEntry.STATUS_OPEN,
                amount_czk__gt=0,
            )
            .first()
        )

    if not selected_entry:
        qr_charge_id = (request.GET.get('qr_charge') or '').strip()
        if qr_charge_id.isdigit():
            selected_charge = next((charge for charge in sale_charges if str(charge.id) == qr_charge_id), None)
            if selected_charge and not selected_charge.paid:
                selected_entry = (
                    ChildFinanceEntry.objects
                    .filter(
                        child=child,
                        event_type=ChildFinanceEntry.TYPE_SALE,
                        status=ChildFinanceEntry.STATUS_OPEN,
                        amount_czk=selected_charge.amount_czk,
                    )
                    .order_by('-id')
                    .first()
                )

    if not selected_entry:
        qr_proforma_id = (request.GET.get('qr_proforma') or '').strip()
        if qr_proforma_id.isdigit():
            selected_entry = (
                ChildFinanceEntry.objects
                .filter(
                    id=qr_proforma_id,
                    child=child,
                    event_type=ChildFinanceEntry.TYPE_PROFORMA,
                    direction=ChildFinanceEntry.DIR_DEBIT,
                    status=ChildFinanceEntry.STATUS_OPEN,
                )
                .first()
            )

    attendance_rows = list(
        Attendance.objects
        .filter(child=child)
        .select_related('session__group', 'session__group__sport')
        .order_by('-session__date', '-id')
    )
    open_debit = ChildFinanceEntry.objects.filter(
        child=child,
        direction=ChildFinanceEntry.DIR_DEBIT,
        status=ChildFinanceEntry.STATUS_OPEN,
    )
    due_total = sum((entry.amount_czk for entry in open_debit), Decimal('0.00'))
    qr_amount = due_total
    qr_title = 'Souhrnná úhrada'
    qr_message = 'Souhrnná úhrada členství a položek'
    if selected_entry:
        qr_amount = selected_entry.amount_czk
        if selected_entry.event_type in (ChildFinanceEntry.TYPE_PROFORMA, ChildFinanceEntry.TYPE_INVOICE):
            qr_title = 'Členství'
            qr_message = 'Členství'
        else:
            qr_title = selected_entry.title
            qr_message = selected_entry.title or 'Úhrada'
    if not isinstance(qr_amount, Decimal):
        try:
            qr_amount = Decimal(qr_amount)
        except (TypeError, InvalidOperation):
            qr_amount = Decimal('0.00')

    return render(request, 'parent/child_detail.html', {
        'child': child,
        'form': form,
        'memberships': memberships,
        'sale_charges': sale_charges,
        'finance_entries': ChildFinanceEntry.objects.filter(child=child).select_related('membership', 'membership__group').order_by('-occurred_on', '-id'),
        'attendance_rows': attendance_rows,
        'due_total': due_total,
        'qr_amount': qr_amount,
        'qr_title': qr_title,
        'qr_message': qr_message,
        'selected_entry': selected_entry,
        'documents': ClubDocument.objects.order_by('-uploaded_at', '-id')[:30],
    })


@role_required('parent')
def parent_child_history(request, child_id):
    child = get_object_or_404(Child, id=child_id, parent=request.user)
    entries = list(
        ChildFinanceEntry.objects
        .filter(child=child)
        .select_related('membership', 'membership__group')
        .order_by('-occurred_on', '-id')
    )
    for entry in entries:
        if entry.event_type in (ChildFinanceEntry.TYPE_PROFORMA, ChildFinanceEntry.TYPE_INVOICE):
            entry.display_title = 'Členství'
        else:
            entry.display_title = entry.title

        if entry.direction == ChildFinanceEntry.DIR_DEBIT and entry.status == ChildFinanceEntry.STATUS_OPEN:
            entry.parent_status_label = 'Nezaplaceno'
        elif entry.status == ChildFinanceEntry.STATUS_CLOSED and entry.direction == ChildFinanceEntry.DIR_DEBIT:
            entry.parent_status_label = 'Uhrazeno'
        elif entry.status == ChildFinanceEntry.STATUS_CLOSED and entry.direction == ChildFinanceEntry.DIR_CREDIT:
            entry.parent_status_label = 'Přijato'
        elif entry.status == ChildFinanceEntry.STATUS_CANCELLED:
            entry.parent_status_label = 'Storno'
        else:
            entry.parent_status_label = entry.get_status_display()

    return render(request, 'parent/child_history.html', {
        'child': child,
        'entries': entries,
    })


@role_required('parent')
def parent_proforma_detail(request, entry_id):
    entry = get_object_or_404(
        ChildFinanceEntry.objects
        .select_related('child', 'child__parent', 'membership', 'membership__group', 'membership__group__sport')
        .filter(event_type=ChildFinanceEntry.TYPE_PROFORMA),
        id=entry_id,
        child__parent=request.user,
    )
    qr_amount = entry.amount_czk if (
        entry.direction == ChildFinanceEntry.DIR_DEBIT
        and entry.status == ChildFinanceEntry.STATUS_OPEN
        and entry.amount_czk > 0
    ) else Decimal('0.00')
    return render(request, 'parent/proforma_detail.html', {
        'entry': entry,
        'qr_amount': qr_amount,
    })
