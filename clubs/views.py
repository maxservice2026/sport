import csv
import json
import logging
import smtplib
import ssl
from datetime import date, timedelta, datetime
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.contrib import messages
from django.contrib.auth import login
from django.db import transaction
from django.forms import inlineformset_factory
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.db.models import Q, Count, IntegerField, Prefetch
from django.db.models.functions import Cast
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone

from users.utils import role_required
from .forms import (
    RegistrationForm,
    GroupAdminForm,
    AttendanceOptionForm,
    ChildEditForm,
    AdminMembershipAddForm,
    ReceivedPaymentForm,
    ClubDocumentForm,
    DataCompletionLookupForm,
    DataCompletionUpdateForm,
)
from .models import (
    Group,
    AttendanceOption,
    Membership,
    Child,
    ReceivedPayment,
    ChildArchiveLog,
    ClubDocument,
    ChildFinanceEntry,
    ChildConsent,
)
from .pricing import FULL_PERIOD_MONTHS, group_month_starts, month_start, normalize_start_month, payable_months_count, prorated_amount
from .payments import build_payment_counter, consume_matching_payment
from attendance.models import Attendance
from users.utils import get_app_settings

logger = logging.getLogger(__name__)


DAY_TO_WEEKDAY = {
    'Po': 0,
    'Út': 1,
    'St': 2,
    'Čt': 3,
    'Pá': 4,
    'So': 5,
    'Ne': 6,
}


def _allowed_start_months(group):
    current_month = date.today().replace(day=1)
    return [m for m in group_month_starts(group) if m >= current_month]


def _send_registration_confirmation_email(target_email):
    if not target_email:
        return False

    settings_obj = get_app_settings()
    subject = (settings_obj.registration_confirmation_subject or '').strip() or 'SK MNÍŠECKO - potvrzujeme přijetí registrace'
    body = (settings_obj.registration_confirmation_body or '').strip() or 'Dobrý den, děkujeme za zaslání registrace. Tým SK Mníšecko.'

    smtp_host = (settings_obj.payment_smtp_host or '').strip()
    smtp_port = int(settings_obj.payment_smtp_port or 0)
    smtp_user = (settings_obj.payment_smtp_user or '').strip()
    smtp_password = settings_obj.payment_smtp_password or ''
    if not all([smtp_host, smtp_port, smtp_user, smtp_password]):
        return False

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = smtp_user
    message['To'] = target_email
    message.set_content(body)

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(
                smtp_host,
                smtp_port,
                timeout=10,
                context=ssl.create_default_context(),
            ) as client:
                client.login(smtp_user, smtp_password)
                client.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as client:
                client.ehlo()
                if smtp_port in (25, 587):
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                client.login(smtp_user, smtp_password)
                client.send_message(message)
        return True
    except Exception:
        logger.exception('Nepodařilo se odeslat potvrzení registrace na %s', target_email)
        return False


def _next_reference(prefix):
    stamp = timezone.localtime().strftime('%Y%m%d%H%M%S')
    return f"{prefix}-{stamp}"


def _create_finance_entry(
    *,
    child,
    membership=None,
    event_type=ChildFinanceEntry.TYPE_INFO,
    direction=ChildFinanceEntry.DIR_DEBIT,
    status=ChildFinanceEntry.STATUS_OPEN,
    title='',
    amount=Decimal('0.00'),
    variable_symbol='',
    note='',
    created_by=None,
    reference='',
):
    return ChildFinanceEntry.objects.create(
        child=child,
        membership=membership,
        event_type=event_type,
        direction=direction,
        status=status,
        title=title,
        amount_czk=Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
        variable_symbol=variable_symbol or child.variable_symbol,
        note=note,
        created_by=created_by,
        reference_code=reference,
        occurred_on=timezone.localdate(),
    )


def _issue_membership_proforma(membership, *, created_by=None):
    base_price = membership.attendance_option.price_czk if membership.attendance_option else Decimal('0.00')
    selected_start = membership.billing_start_month
    if not selected_start and membership.registered_at:
        selected_start = month_start(membership.registered_at.date())
    effective_start = normalize_start_month(
        membership.group,
        selected_start_month=selected_start,
        fallback_date=date.today(),
    )
    payable_months = payable_months_count(
        membership.group,
        selected_start_month=effective_start,
        fallback_date=date.today(),
    )
    due_amount = prorated_amount(base_price, payable_months)
    if due_amount <= 0:
        return None

    existing_open = ChildFinanceEntry.objects.filter(
        membership=membership,
        event_type=ChildFinanceEntry.TYPE_PROFORMA,
        status=ChildFinanceEntry.STATUS_OPEN,
    ).first()
    if existing_open:
        return existing_open

    title = f"Záloha: {membership.group.sport.name} - {membership.group.name} ({payable_months}/{FULL_PERIOD_MONTHS} měs.)"
    note = f"Varianta: {membership.attendance_option.name if membership.attendance_option else '-'} | Od: {effective_start:%m/%Y}"
    return _create_finance_entry(
        child=membership.child,
        membership=membership,
        event_type=ChildFinanceEntry.TYPE_PROFORMA,
        direction=ChildFinanceEntry.DIR_DEBIT,
        status=ChildFinanceEntry.STATUS_OPEN,
        title=title,
        amount=due_amount,
        variable_symbol=membership.child.variable_symbol,
        note=note,
        created_by=created_by,
        reference=_next_reference('ZAL'),
    )


def _match_payment_to_open_proforma(payment, *, created_by=None):
    open_entries = (
        ChildFinanceEntry.objects
        .filter(
            event_type=ChildFinanceEntry.TYPE_PROFORMA,
            status=ChildFinanceEntry.STATUS_OPEN,
            variable_symbol=str(payment.variable_symbol),
            amount_czk=payment.amount_czk,
        )
        .order_by('id')
    )
    for entry in open_entries:
        entry.status = ChildFinanceEntry.STATUS_CLOSED
        entry.save(update_fields=['status'])

        _create_finance_entry(
            child=entry.child,
            membership=entry.membership,
            event_type=ChildFinanceEntry.TYPE_PAYMENT,
            direction=ChildFinanceEntry.DIR_CREDIT,
            status=ChildFinanceEntry.STATUS_CLOSED,
            title='Příchozí platba',
            amount=payment.amount_czk,
            variable_symbol=payment.variable_symbol,
            note=f"{payment.sender_name or ''} {payment.note or ''}".strip(),
            created_by=created_by,
            reference=_next_reference('PLT'),
        )
        _create_finance_entry(
            child=entry.child,
            membership=entry.membership,
            event_type=ChildFinanceEntry.TYPE_INVOICE,
            direction=ChildFinanceEntry.DIR_DEBIT,
            status=ChildFinanceEntry.STATUS_CLOSED,
            title=f"Faktura k záloze {entry.reference_code or ''}".strip(),
            amount=entry.amount_czk,
            variable_symbol=entry.variable_symbol,
            note=entry.note,
            created_by=created_by,
            reference=_next_reference('FAK'),
        )
        return entry
    return None


def public_register(request):
    parent_user = request.user if request.user.is_authenticated and request.user.role == 'parent' else None
    form = RegistrationForm(request.POST or None, parent_user=parent_user)
    if request.method == 'POST' and form.is_valid():
        parent, child, membership, created_parent, created_child, membership_created = form.save()
        confirmation_sent = _send_registration_confirmation_email(parent.email)

        # Registrace z veřejného formuláře = okamžitá autorizace členské zálohy,
        # aby se částka hned zobrazila v rodičovském přehledu.
        for payload in getattr(form, '_membership_payload', []):
            payload_group = payload.get('group')
            if not payload_group:
                continue
            membership_obj = (
                Membership.objects
                .select_related('attendance_option', 'group')
                .filter(child=child, group=payload_group, active=True)
                .first()
            )
            if membership_obj:
                _issue_membership_proforma(membership_obj, created_by=parent)

        ChildArchiveLog.objects.create(
            child=child,
            actor=parent,
            event_type=ChildArchiveLog.EVENT_REGISTRATION,
            message='Veřejná registrace / aktualizace údajů rodičem.',
        )
        login(request, parent)
        if not created_child and membership_created:
            messages.info(request, 'Dítě již existovalo a bylo přiřazeno do nové skupiny.')
        elif not membership_created:
            messages.warning(request, 'Dítě je již v této skupině. Vyberte jinou skupinu.')
        else:
            messages.success(
                request,
                'Děkujeme za registraci. Shrnutí posíláme na email. '
                'Uložte si přístupové údaje v prohlížeči pro další přihlášení.',
            )
            if not confirmation_sent:
                messages.warning(request, 'Potvrzovací e-mail se nyní nepodařilo odeslat. Zkontrolujte nastavení SMTP.')
        return redirect('parent_dashboard')
    return render(request, 'public/register.html', {'form': form})


def attendance_options_api(request):
    group_id = request.GET.get('group_id')
    if not group_id:
        return JsonResponse({'options': [], 'months': [], 'group': None})
    group = Group.objects.filter(id=group_id).first()
    options = AttendanceOption.objects.filter(group_id=group_id).values('id', 'name')
    months = []
    group_payload = None
    if group:
        months = [
            {'value': m.strftime('%Y-%m'), 'label': m.strftime('%m/%Y')}
            for m in _allowed_start_months(group)
        ]
        spots = group.free_slots
        group_payload = {
            'id': group.id,
            'name': str(group),
            'registration_state': group.registration_state,
            'max_members': group.max_members,
            'free_slots': spots,
        }
    return JsonResponse({'options': list(options), 'months': months, 'group': group_payload})


def address_lookup_api(request):
    query = (request.GET.get('q') or '').strip()
    if len(query) < 4:
        return JsonResponse({'results': []})

    params = urlencode({
        'q': query,
        'format': 'jsonv2',
        'addressdetails': 1,
        'countrycodes': 'cz',
        'limit': 8,
    })
    url = f'https://nominatim.openstreetmap.org/search?{params}'
    req = Request(
        url,
        headers={
            'User-Agent': 'SK-Mnisecko-Registration/1.0',
            'Accept': 'application/json',
        },
    )

    try:
        with urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='ignore'))
    except Exception:
        return JsonResponse({'results': []})

    results = []
    seen = set()
    for item in payload if isinstance(payload, list) else []:
        address = item.get('address') or {}
        road = (
            address.get('road')
            or address.get('pedestrian')
            or address.get('residential')
            or address.get('footway')
            or ''
        ).strip()
        house_no = (address.get('house_number') or '').strip()
        city = (
            address.get('city')
            or address.get('town')
            or address.get('village')
            or address.get('municipality')
            or address.get('hamlet')
            or ''
        ).strip()
        postcode = (address.get('postcode') or '').replace(' ', '')

        street = ' '.join([part for part in (road, house_no) if part]).strip()
        if not street:
            display_name = (item.get('display_name') or '').split(',')[0].strip()
            street = display_name
        if not street:
            continue

        label = street
        if city or postcode:
            label = f"{street}, {postcode} {city}".strip()

        key = (street.lower(), city.lower(), postcode)
        if key in seen:
            continue
        seen.add(key)

        results.append({
            'label': label,
            'street': street,
            'city': city,
            'postcode': postcode,
        })

    return JsonResponse({'results': results})


def public_data_completion(request):
    lookup_form = DataCompletionLookupForm(request.GET or None)
    children = Child.objects.none()
    selected_child = None
    completion_form = None

    if lookup_form.is_valid():
        last_name = (lookup_form.cleaned_data.get('last_name') or '').strip()
        first_name = (lookup_form.cleaned_data.get('first_name') or '').strip()
        vs = (lookup_form.cleaned_data.get('variable_symbol') or '').strip()
        children = Child.objects.select_related('parent').prefetch_related('memberships__group', 'memberships__attendance_option')
        if last_name:
            children = children.filter(last_name__icontains=last_name)
        if first_name:
            children = children.filter(first_name__icontains=first_name)
        if vs:
            children = children.filter(variable_symbol__icontains=vs)
        children = children.order_by('last_name', 'first_name')[:50]

    selected_child_id = request.GET.get('child_id') or request.POST.get('child_id')
    if selected_child_id:
        selected_child = Child.objects.select_related('parent').prefetch_related('memberships__group', 'memberships__attendance_option').filter(id=selected_child_id).first()
    if selected_child:
        initial = {
            'parent_email': selected_child.parent.email if selected_child.parent else '',
            'parent_phone': selected_child.parent.phone if selected_child.parent else '',
            'parent_street': selected_child.parent.street if selected_child.parent else '',
            'parent_city': selected_child.parent.city if selected_child.parent else '',
            'parent_zip': selected_child.parent.zip_code if selected_child.parent else '',
            'child_birth_number': selected_child.birth_number or '',
            'child_passport_number': selected_child.passport_number or '',
        }
        completion_form = DataCompletionUpdateForm(request.POST or None, initial=initial)
        if request.method == 'POST' and completion_form.is_valid():
            data = completion_form.cleaned_data
            parent = selected_child.parent
            parent.email = data['parent_email']
            parent.phone = data['parent_phone']
            parent.street = data['parent_street']
            parent.city = data['parent_city']
            parent.zip_code = data['parent_zip']
            parent.save(update_fields=['email', 'phone', 'street', 'city', 'zip_code'])

            selected_child.birth_number = data.get('child_birth_number') or None
            selected_child.passport_number = data.get('child_passport_number') or None
            selected_child.save(update_fields=['birth_number', 'passport_number'])

            ChildConsent.objects.create(
                child=selected_child,
                parent=parent,
                consent_vop=data['consent_vop'],
                consent_gdpr=data['consent_gdpr'],
                consent_health=data['consent_health'],
                source=ChildConsent.SOURCE_COMPLETION,
            )
            ChildArchiveLog.objects.create(
                child=selected_child,
                actor=parent,
                event_type=ChildArchiveLog.EVENT_PROFILE,
                message='Doplněné chybějící údaje rodičem přes formulář doplnění.',
            )
            messages.success(request, 'Děkujeme, údaje byly doplněny.')
            return redirect(f"{reverse('public_data_completion')}?child_id={selected_child.id}")

    return render(request, 'public/data_completion.html', {
        'lookup_form': lookup_form,
        'children': children,
        'selected_child': selected_child,
        'completion_form': completion_form,
    })


def _groups_nav():
    return Group.objects.select_related('sport').order_by('sport__name', 'name')


def _group_training_dates_to_today(group):
    if not group.start_date or not group.end_date:
        return []
    if group.start_date > group.end_date:
        return []

    end = min(group.end_date, date.today())
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


def _group_attendance_percent_map(group, child_ids):
    training_dates = _group_training_dates_to_today(group)
    total = len(training_dates)
    if total == 0:
        return {child_id: 0 for child_id in child_ids}

    present_rows = (
        Attendance.objects
        .filter(session__group=group, session__date__in=training_dates, child_id__in=child_ids, present=True)
        .values('child_id')
        .annotate(cnt=Count('id'))
    )
    present_map = {row['child_id']: row['cnt'] for row in present_rows}
    return {child_id: int((present_map.get(child_id, 0) / total) * 100) for child_id in child_ids}


def _group_avg_attendance_percent(group):
    memberships = list(Membership.objects.filter(group=group, active=True).only('child_id'))
    if not memberships:
        return 0
    child_ids = [m.child_id for m in memberships]
    values = _group_attendance_percent_map(group, child_ids)
    if not values:
        return 0
    return int(round(sum(values.values()) / len(values)))


@role_required('admin')
def admin_group_list(request):
    groups = list(Group.objects.select_related('sport').prefetch_related('trainers').order_by('sport__name', 'name'))

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'archive_groups':
            selected_ids = request.POST.getlist('selected_groups')
            selected_groups = [g for g in groups if str(g.id) in selected_ids]
            if not selected_groups:
                messages.warning(request, 'Vyberte alespoň jednu skupinu pro archiv.')
                return redirect('admin_groups')

            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = 'attachment; filename="archiv_skupin.csv"'
            response.write('\ufeff')
            writer = csv.writer(response, delimiter=';')
            writer.writerow([
                'Sport',
                'Skupina',
                'Obdobi_od',
                'Obdobi_do',
                'Jmeno',
                'Prijmeni',
                'VS',
                'Rodne_cislo',
                'Telefon_rodice',
                'Varianta',
                'Zarazeni_od',
                'Aktivni',
                'Dochazka_%',
            ])
            for group in selected_groups:
                memberships = list(
                    Membership.objects.filter(group=group).select_related('child', 'child__parent', 'attendance_option')
                )
                attendance_map = _group_attendance_percent_map(group, [m.child_id for m in memberships])
                for membership in memberships:
                    writer.writerow([
                        group.sport.name,
                        group.name,
                        group.start_date.isoformat() if group.start_date else '',
                        group.end_date.isoformat() if group.end_date else '',
                        membership.child.first_name,
                        membership.child.last_name,
                        membership.child.variable_symbol,
                        membership.child.birth_number or membership.child.passport_number or '',
                        membership.child.parent.phone if membership.child.parent else '',
                        membership.attendance_option.name if membership.attendance_option else '',
                        membership.registered_at.date().isoformat() if membership.registered_at else '',
                        'ANO' if membership.active else 'NE',
                        attendance_map.get(membership.child_id, 0),
                    ])
            return response

        if action == 'delete_groups':
            selected_ids = request.POST.getlist('selected_groups')
            selected_groups = [g for g in groups if str(g.id) in selected_ids]
            if not selected_groups:
                messages.warning(request, 'Vyberte alespoň jednu skupinu ke smazání.')
                return redirect('admin_groups')

            deleted_count = 0
            with transaction.atomic():
                for group in selected_groups:
                    group.delete()
                    deleted_count += 1

            messages.success(request, f'Smazáno skupin: {deleted_count}.')
            return redirect('admin_groups')

        if action == 'clone_groups':
            selected_ids = request.POST.getlist('selected_groups')
            selected_groups = [g for g in groups if str(g.id) in selected_ids]
            if not selected_groups:
                messages.warning(request, 'Vyberte alespoň jednu skupinu pro kopii.')
                return redirect('admin_groups')
            suffix = (request.POST.get('clone_suffix') or '').strip()
            start_raw = (request.POST.get('clone_start_date') or '').strip()
            end_raw = (request.POST.get('clone_end_date') or '').strip()
            if not suffix:
                messages.error(request, 'Vyplňte příznak pro nové skupiny (např. 2026/2).')
                return redirect('admin_groups')
            try:
                new_start = date.fromisoformat(start_raw) if start_raw else None
                new_end = date.fromisoformat(end_raw) if end_raw else None
            except ValueError:
                messages.error(request, 'Neplatné datum pro kopii skupin.')
                return redirect('admin_groups')

            created = 0
            copied_memberships = 0
            with transaction.atomic():
                for source in selected_groups:
                    new_name = f"{source.name} {suffix}"
                    clone, was_created = Group.objects.get_or_create(
                        sport=source.sport,
                        name=new_name,
                        defaults={
                            'training_days': source.training_days,
                            'start_date': new_start,
                            'end_date': new_end,
                            'registration_state': Group.REG_DISABLED,
                            'max_members': source.max_members,
                            'allow_combined_registration': source.allow_combined_registration,
                        },
                    )
                    if was_created:
                        created += 1
                        clone.trainers.set(source.trainers.all())
                        for option in source.attendance_options.all():
                            AttendanceOption.objects.create(
                                group=clone,
                                name=option.name,
                                frequency_per_week=option.frequency_per_week,
                                price_czk=option.price_czk,
                            )
                    option_by_name = {o.name: o for o in clone.attendance_options.all()}
                    for membership in Membership.objects.filter(group=source, active=True).select_related('attendance_option'):
                        target_option = None
                        if membership.attendance_option:
                            target_option = option_by_name.get(membership.attendance_option.name)
                        _, was_membership_created = Membership.objects.get_or_create(
                            child=membership.child,
                            group=clone,
                            defaults={
                                'attendance_option': target_option,
                                'billing_start_month': month_start(new_start) if new_start else membership.billing_start_month,
                                'active': True,
                            },
                        )
                        if was_membership_created:
                            copied_memberships += 1
            messages.success(
                request,
                f'Vytvořeno skupin: {created}. Zkopírovaná členství: {copied_memberships}.',
            )
            return redirect('admin_groups')

    groups_by_sport = {}
    for group in groups:
        groups_by_sport.setdefault(group.sport.name, [])
        groups_by_sport[group.sport.name].append({
            'group': group,
            'members_count': group.active_members_count,
            'avg_attendance_percent': _group_avg_attendance_percent(group),
        })

    return render(request, 'admin/groups_list.html', {
        'groups': groups,
        'groups_by_sport': groups_by_sport,
        'groups_nav': _groups_nav(),
    })


@role_required('admin')
def admin_group_create(request):
    GroupOptionFormSet = inlineformset_factory(
        Group,
        AttendanceOption,
        form=AttendanceOptionForm,
        extra=5,
        max_num=5,
        validate_max=True,
        can_delete=True,
    )
    form = GroupAdminForm(request.POST or None)
    formset = GroupOptionFormSet(request.POST or None)

    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        group = form.save()
        formset.instance = group
        formset.save()
        messages.success(request, 'Skupina byla vytvořena.')
        return redirect('admin_groups')

    return render(request, 'admin/group_form.html', {
        'form': form,
        'formset': formset,
        'is_edit': False,
        'groups_nav': _groups_nav(),
    })


@role_required('admin')
def admin_group_detail(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    query = (request.GET.get('q') or request.POST.get('q') or '').strip()
    memberships_qs = (
        Membership.objects
        .filter(group=group, active=True)
        .select_related('child', 'child__parent', 'attendance_option')
        .order_by('child__last_name', 'child__first_name')
    )
    if query:
        memberships_qs = memberships_qs.filter(
            Q(child__first_name__icontains=query) |
            Q(child__last_name__icontains=query) |
            Q(child__variable_symbol__icontains=query)
        )
    memberships = list(memberships_qs)
    options = group.attendance_options.all().order_by('frequency_per_week', 'name')
    target_groups = Group.objects.select_related('sport').exclude(id=group.id).order_by('sport__name', 'name')
    child_ids = [membership.child_id for membership in memberships]
    attendance_percent_map = _group_attendance_percent_map(group, child_ids)

    if request.method == 'POST':
        action = request.POST.get('action')
        query_from_post = (request.POST.get('q') or '').strip()

        if action == 'update_variant':
            membership_id = request.POST.get('membership_id')
            option_id = request.POST.get('attendance_option') or None
            membership = get_object_or_404(Membership, id=membership_id, group=group)
            if option_id:
                membership.attendance_option = get_object_or_404(AttendanceOption, id=option_id, group=group)
            else:
                membership.attendance_option = None
            membership.save(update_fields=['attendance_option'])
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'ok': True, 'membership_id': membership.id})
            messages.success(request, 'Docházková varianta byla uložena.')

        elif action == 'bulk':
            selected_ids = request.POST.getlist('selected_memberships')
            selected_memberships = (
                Membership.objects
                .filter(id__in=selected_ids, group=group)
                .select_related('child', 'attendance_option')
            )
            bulk_action = request.POST.get('bulk_action')

            if not selected_memberships:
                messages.warning(request, 'Vyberte alespoň jedno dítě.')
            elif bulk_action == 'move':
                target_group_id = request.POST.get('target_group')
                if not target_group_id:
                    messages.warning(request, 'Vyberte cílovou skupinu pro přesun.')
                else:
                    target_group = get_object_or_404(Group, id=target_group_id)
                    moved = 0
                    with transaction.atomic():
                        for membership in selected_memberships:
                            target_option = None
                            if membership.attendance_option_id:
                                target_option = (
                                    AttendanceOption.objects.filter(
                                        group=target_group,
                                        name=membership.attendance_option.name,
                                    ).first()
                                )
                            _, created = Membership.objects.get_or_create(
                                child=membership.child,
                                group=target_group,
                                defaults={
                                    'attendance_option': target_option,
                                    'billing_start_month': membership.billing_start_month,
                                },
                            )
                            membership.delete()
                            moved += 1
                    messages.success(request, f'Přesunuto dětí: {moved}.')
            elif bulk_action == 'copy':
                target_group_id = request.POST.get('target_group')
                if not target_group_id:
                    messages.warning(request, 'Vyberte cílovou skupinu pro kopii.')
                else:
                    target_group = get_object_or_404(Group, id=target_group_id)
                    copied = 0
                    already = 0
                    with transaction.atomic():
                        for membership in selected_memberships:
                            target_option = None
                            if membership.attendance_option_id:
                                target_option = (
                                    AttendanceOption.objects.filter(
                                        group=target_group,
                                        name=membership.attendance_option.name,
                                    ).first()
                                )
                            _, created = Membership.objects.get_or_create(
                                child=membership.child,
                                group=target_group,
                                defaults={
                                    'attendance_option': target_option,
                                    'billing_start_month': membership.billing_start_month,
                                },
                            )
                            if created:
                                copied += 1
                            else:
                                already += 1
                    if already:
                        messages.success(request, f'Zkopírováno: {copied}. Již existovalo: {already}.')
                    else:
                        messages.success(request, f'Zkopírováno dětí: {copied}.')
            elif bulk_action == 'delete':
                child_ids_to_delete = list(selected_memberships.values_list('child_id', flat=True).distinct())
                deleted_count = len(child_ids_to_delete)
                Child.objects.filter(id__in=child_ids_to_delete).delete()
                messages.success(request, f'Smazáno dětí: {deleted_count}.')
            elif bulk_action == 'deactivate':
                child_ids_to_deactivate = list(selected_memberships.values_list('child_id', flat=True).distinct())
                updated = Membership.objects.filter(child_id__in=child_ids_to_deactivate, active=True).update(active=False)
                messages.success(request, f'Ukončená členství: {updated}.')
            else:
                messages.warning(request, 'Vyberte akci nad označenými dětmi.')

        redirect_url = reverse('admin_group_detail', kwargs={'group_id': group.id})
        final_query = query_from_post or query
        if final_query:
            redirect_url += f'?{urlencode({"q": final_query})}'
        return redirect(redirect_url)

    rows = []
    for membership in memberships:
        rows.append({
            'membership': membership,
            'phone': membership.child.parent.phone if membership.child.parent else '',
            'attendance_percent': attendance_percent_map.get(membership.child_id, 0),
        })

    return render(request, 'admin/group_detail.html', {
        'group': group,
        'rows': rows,
        'query': query,
        'options': options,
        'target_groups': target_groups,
        'groups_nav': _groups_nav(),
        'admin_wide_content': True,
    })


@role_required('admin')
def admin_group_edit(request, group_id):
    group = get_object_or_404(Group, id=group_id)

    if request.method == 'POST' and request.POST.get('action') == 'reassign_option':
        blocked_option_ids = []
        for raw in request.POST.getlist('blocked_option_ids'):
            try:
                blocked_option_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        blocked_option_ids = sorted(set(blocked_option_ids))
        if not blocked_option_ids:
            messages.warning(request, 'Nebyla vybrána žádná varianta k úpravě.')
            return redirect('admin_group_edit', group_id=group.id)

        allowed_option_ids = set(
            AttendanceOption.objects.filter(group=group).exclude(id__in=blocked_option_ids).values_list('id', flat=True)
        )
        memberships = (
            Membership.objects
            .filter(group=group, attendance_option_id__in=blocked_option_ids, active=True)
            .select_related('attendance_option')
        )

        updated = 0
        missing = 0
        invalid = 0
        for membership in memberships:
            target_raw = request.POST.get(f'membership_option_{membership.id}')
            if not target_raw:
                missing += 1
                continue
            try:
                target_id = int(target_raw)
            except (TypeError, ValueError):
                invalid += 1
                continue
            if target_id not in allowed_option_ids:
                invalid += 1
                continue
            if membership.attendance_option_id != target_id:
                membership.attendance_option_id = target_id
                membership.save(update_fields=['attendance_option'])
                updated += 1

        if updated:
            messages.success(request, f'Uloženo změn variant: {updated}. Nyní můžete původní variantu bezpečně smazat.')
        if missing:
            messages.warning(request, f'U {missing} dětí nebyla vybrána nová varianta.')
        if invalid:
            messages.error(request, f'U {invalid} dětí byla zadaná neplatná cílová varianta.')
        return redirect('admin_group_edit', group_id=group.id)

    extra_forms = max(0, 5 - group.attendance_options.count())
    GroupOptionFormSet = inlineformset_factory(
        Group,
        AttendanceOption,
        form=AttendanceOptionForm,
        extra=extra_forms,
        max_num=5,
        validate_max=True,
        can_delete=True,
    )
    form = GroupAdminForm(request.POST or None, instance=group)
    formset = GroupOptionFormSet(request.POST or None, instance=group)
    blocked_option_ids = []
    blocked_memberships = []
    replacement_options = []
    blocked_deletions = []

    if request.method == 'POST':
        form_valid = form.is_valid()
        formset_valid = formset.is_valid()
        if form_valid and formset_valid:
            for option_form in formset.forms:
                cleaned = getattr(option_form, 'cleaned_data', {}) or {}
                if cleaned.get('DELETE') and option_form.instance and option_form.instance.pk:
                    blocked_option_ids.append(option_form.instance.pk)
            blocked_option_ids = sorted(set(blocked_option_ids))

            if blocked_option_ids:
                blocked_memberships = list(
                    Membership.objects
                    .filter(group=group, attendance_option_id__in=blocked_option_ids, active=True)
                    .select_related('child', 'attendance_option')
                    .order_by('attendance_option__name', 'child__last_name', 'child__first_name')
                )
                if blocked_memberships:
                    replacement_options = list(
                        AttendanceOption.objects
                        .filter(group=group)
                        .exclude(id__in=blocked_option_ids)
                        .order_by('frequency_per_week', 'name')
                    )
                    blocked_map = {}
                    for membership in blocked_memberships:
                        option = membership.attendance_option
                        if option.id not in blocked_map:
                            blocked_map[option.id] = {'option': option, 'memberships': []}
                        blocked_map[option.id]['memberships'].append(membership)
                    blocked_deletions = list(blocked_map.values())

                    messages.error(
                        request,
                        'Tyto docházkové varianty nelze smazat, protože je mají vybrané děti. '
                        'Nejprve dětem nastavte jinou variantu níže.'
                    )
                else:
                    blocked_option_ids = []

            if not blocked_memberships:
                group = form.save()
                formset.save()
                messages.success(request, 'Skupina byla uložena.')
                return redirect('admin_group_detail', group_id=group.id)
        elif form_valid:
            messages.warning(request, 'Docházkové varianty mají chyby. Skupina zatím nebyla uložena.')

    return render(request, 'admin/group_form.html', {
        'form': form,
        'formset': formset,
        'group': group,
        'is_edit': True,
        'blocked_option_ids': blocked_option_ids,
        'blocked_memberships': blocked_memberships,
        'replacement_options': replacement_options,
        'blocked_deletions': blocked_deletions,
        'groups_nav': _groups_nav(),
    })


@role_required('admin')
def admin_child_edit(request, child_id):
    child = get_object_or_404(Child, id=child_id)
    form = ChildEditForm(request.POST or None, instance=child)
    membership_form = AdminMembershipAddForm(request.POST or None)
    memberships = Membership.objects.filter(child=child).select_related('group', 'attendance_option').order_by('-active', 'group__name')
    if request.method == 'POST':
        if 'update_membership_date' in request.POST:
            membership_id = request.POST.get('membership_id')
            membership = get_object_or_404(Membership, id=membership_id, child=child)
            registered_date_raw = (request.POST.get('registered_at_date') or '').strip()
            if not registered_date_raw:
                messages.error(request, 'Vyplňte datum zařazení.')
                return redirect('admin_child_edit', child_id=child.id)
            try:
                registered_date = date.fromisoformat(registered_date_raw)
            except ValueError:
                messages.error(request, 'Neplatné datum zařazení.')
                return redirect('admin_child_edit', child_id=child.id)

            current_dt = membership.registered_at or timezone.now()
            new_dt = datetime.combine(registered_date, current_dt.time().replace(microsecond=0))
            if timezone.is_aware(current_dt):
                new_dt = timezone.make_aware(new_dt, timezone.get_current_timezone())

            membership.registered_at = new_dt
            membership.save(update_fields=['registered_at'])
            ChildArchiveLog.objects.create(
                child=child,
                actor=request.user,
                event_type=ChildArchiveLog.EVENT_MEMBERSHIP,
                message=f"Změněno datum zařazení do skupiny {membership.group} na {registered_date:%d.%m.%Y}.",
            )
            messages.success(request, 'Datum zařazení do skupiny bylo uloženo automaticky.')
            return redirect('admin_child_edit', child_id=child.id)

        if 'end_membership' in request.POST:
            membership_id = request.POST.get('membership_id')
            end_mode = (request.POST.get('end_mode') or 'without_refund').strip()
            membership = get_object_or_404(Membership, id=membership_id, child=child, active=True)
            refund_raw = (request.POST.get('refund_amount') or '0').strip().replace(',', '.')
            refund_amount = Decimal('0.00')
            if end_mode == 'with_refund':
                try:
                    refund_amount = Decimal(refund_raw).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                except Exception:
                    messages.error(request, 'Neplatná částka vratky.')
                    return redirect('admin_child_edit', child_id=child.id)
                if refund_amount <= 0:
                    messages.error(request, 'Částka vratky musí být vyšší než 0.')
                    return redirect('admin_child_edit', child_id=child.id)

            membership.active = False
            membership.save(update_fields=['active'])
            ChildFinanceEntry.objects.filter(
                membership=membership,
                event_type=ChildFinanceEntry.TYPE_PROFORMA,
                status=ChildFinanceEntry.STATUS_OPEN,
            ).update(status=ChildFinanceEntry.STATUS_CANCELLED)

            if end_mode == 'with_refund':
                _create_finance_entry(
                    child=child,
                    membership=membership,
                    event_type=ChildFinanceEntry.TYPE_REFUND,
                    direction=ChildFinanceEntry.DIR_CREDIT,
                    status=ChildFinanceEntry.STATUS_CLOSED,
                    title=f"Ukončení členství s vratkou ({membership.group})",
                    amount=refund_amount,
                    variable_symbol=child.variable_symbol,
                    note='Vrácení přeplatku po ukončení členství.',
                    created_by=request.user,
                    reference=_next_reference('VRATKA'),
                )
                ChildArchiveLog.objects.create(
                    child=child,
                    actor=request.user,
                    event_type=ChildArchiveLog.EVENT_MEMBERSHIP,
                    message=f"Ukončeno členství ve skupině {membership.group} s vratkou {refund_amount} Kč.",
                )
                messages.success(request, 'Členství bylo ukončeno s vratkou.')
            else:
                _create_finance_entry(
                    child=child,
                    membership=membership,
                    event_type=ChildFinanceEntry.TYPE_MEMBERSHIP_END,
                    direction=ChildFinanceEntry.DIR_CREDIT,
                    status=ChildFinanceEntry.STATUS_CLOSED,
                    title=f"Ukončení členství bez vratky ({membership.group})",
                    amount=Decimal('0.00'),
                    variable_symbol=child.variable_symbol,
                    note='Ukončeno bez finanční vratky.',
                    created_by=request.user,
                    reference=_next_reference('END'),
                )
                ChildArchiveLog.objects.create(
                    child=child,
                    actor=request.user,
                    event_type=ChildArchiveLog.EVENT_MEMBERSHIP,
                    message=f"Ukončeno členství ve skupině {membership.group} bez vratky.",
                )
                messages.success(request, 'Členství bylo ukončeno bez vratky.')
            return redirect('admin_child_edit', child_id=child.id)

        if 'add_membership' in request.POST and membership_form.is_valid():
            group = membership_form.cleaned_data['group']
            attendance_option = membership_form.cleaned_data.get('attendance_option')
            membership, created = Membership.objects.get_or_create(
                child=child,
                group=group,
                defaults={'attendance_option': attendance_option},
            )
            if not created and attendance_option and membership.attendance_option_id != attendance_option.id:
                membership.attendance_option = attendance_option
                membership.save()
            membership.active = True
            membership.save(update_fields=['active', 'attendance_option'])
            ChildArchiveLog.objects.create(
                child=child,
                actor=request.user,
                event_type=ChildArchiveLog.EVENT_MEMBERSHIP,
                message=f"Přiřazeno do skupiny {group}.",
            )
            messages.success(request, 'Dítě bylo přiřazeno do skupiny.')
            return redirect('admin_child_edit', child_id=child.id)
        elif 'save_child' in request.POST and form.is_valid():
            form.save()
            ChildArchiveLog.objects.create(
                child=child,
                actor=request.user,
                event_type=ChildArchiveLog.EVENT_PROFILE,
                message='Upravené údaje dítěte v kartě.',
            )
            messages.success(request, 'Údaje dítěte byly uloženy.')
            return redirect('admin_child_edit', child_id=child.id)

        if 'add_sale_charge' in request.POST:
            title = (request.POST.get('sale_title') or '').strip()
            amount_raw = (request.POST.get('sale_amount') or '').strip().replace(',', '.')
            note = (request.POST.get('sale_note') or '').strip()
            if not title:
                messages.error(request, 'Vyplňte název prodejní položky.')
                return redirect('admin_child_edit', child_id=child.id)
            try:
                amount_value = Decimal(amount_raw).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            except Exception:
                messages.error(request, 'Neplatná částka prodejní položky.')
                return redirect('admin_child_edit', child_id=child.id)
            if amount_value <= 0:
                messages.error(request, 'Částka musí být větší než 0.')
                return redirect('admin_child_edit', child_id=child.id)

            from .models import SaleCharge
            sale = SaleCharge.objects.create(
                child=child,
                title=title,
                amount_czk=amount_value,
                note=note,
                created_by=request.user,
            )
            _create_finance_entry(
                child=child,
                event_type=ChildFinanceEntry.TYPE_SALE,
                direction=ChildFinanceEntry.DIR_DEBIT,
                status=ChildFinanceEntry.STATUS_OPEN,
                title=f"Prodej: {sale.title}",
                amount=sale.amount_czk,
                variable_symbol=child.variable_symbol,
                note=sale.note,
                created_by=request.user,
                reference=_next_reference('PRODEJ'),
            )
            messages.success(request, 'Prodejní položka byla přidána.')
            return redirect('admin_child_edit', child_id=child.id)

        if 'toggle_sale_paid' in request.POST:
            sale_id = request.POST.get('sale_id')
            from .models import SaleCharge
            sale = get_object_or_404(SaleCharge, id=sale_id, child=child)
            mark_paid = request.POST.get('mark_paid') == '1'
            if mark_paid:
                _create_finance_entry(
                    child=child,
                    event_type=ChildFinanceEntry.TYPE_PAYMENT,
                    direction=ChildFinanceEntry.DIR_CREDIT,
                    status=ChildFinanceEntry.STATUS_CLOSED,
                    title=f"Úhrada prodeje: {sale.title}",
                    amount=sale.amount_czk,
                    variable_symbol=child.variable_symbol,
                    note='Ručně potvrzená úhrada prodejní položky.',
                    created_by=request.user,
                    reference=_next_reference('PLT'),
                )
                ChildFinanceEntry.objects.filter(
                    child=child,
                    event_type=ChildFinanceEntry.TYPE_SALE,
                    status=ChildFinanceEntry.STATUS_OPEN,
                    title=f"Prodej: {sale.title}",
                    amount_czk=sale.amount_czk,
                ).update(status=ChildFinanceEntry.STATUS_CLOSED)
            else:
                ChildFinanceEntry.objects.filter(
                    child=child,
                    event_type=ChildFinanceEntry.TYPE_SALE,
                    title=f"Prodej: {sale.title}",
                    amount_czk=sale.amount_czk,
                ).update(status=ChildFinanceEntry.STATUS_OPEN)
            messages.success(request, 'Stav prodejní položky byl změněn.')
            return redirect('admin_child_edit', child_id=child.id)

    finance_entries = (
        ChildFinanceEntry.objects
        .filter(child=child)
        .select_related('membership', 'membership__group')
        .order_by('-occurred_on', '-id')
    )
    from .models import SaleCharge
    sale_charges = list(SaleCharge.objects.filter(child=child).order_by('-created_at', '-id'))
    for sale in sale_charges:
        sale.paid = ChildFinanceEntry.objects.filter(
            child=child,
            event_type=ChildFinanceEntry.TYPE_PAYMENT,
            title=f"Úhrada prodeje: {sale.title}",
            amount_czk=sale.amount_czk,
        ).exists()
    return render(request, 'admin/child_form.html', {
        'child': child,
        'form': form,
        'membership_form': membership_form,
        'memberships': memberships,
        'finance_entries': finance_entries,
        'sale_charges': sale_charges,
        'groups_nav': _groups_nav(),
    })


@role_required('admin')
def admin_children_list(request):
    query = (request.GET.get('q') or '').strip()
    group_filters = [g for g in request.GET.getlist('groups') if g]
    sort = (request.GET.get('sort') or 'name').strip()
    direction = (request.GET.get('dir') or 'asc').strip().lower()
    rows = _children_rows(query=query, group_filters=group_filters, sort=sort, direction=direction)
    return render(request, 'admin/children_list.html', {
        'rows': rows,
        'query': query,
        'group_filters': group_filters,
        'sort': sort,
        'direction': direction,
        'groups': Group.objects.select_related('sport').order_by('sport__name', 'name'),
        'groups_nav': _groups_nav(),
        'admin_wide_content': True,
    })


def _children_rows(query='', group_filters=None, sort='name', direction='asc'):
    query = (query or '').strip()
    group_filters = [str(v) for v in (group_filters or []) if str(v).strip()]

    memberships_prefetch = Prefetch(
        'memberships',
        queryset=Membership.objects.select_related('group').order_by('registered_at', 'id'),
    )
    children = Child.objects.select_related('parent').prefetch_related(memberships_prefetch)
    if group_filters:
        children = children.filter(memberships__group_id__in=group_filters)
    if query:
        children = children.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(variable_symbol__icontains=query) |
            Q(unique_id__icontains=query) |
            Q(memberships__group__name__icontains=query)
        )
    children = list(children.distinct().order_by('last_name', 'first_name'))

    def infer_birth_year(child):
        if not child.birth_number:
            return '-'
        prefix = child.birth_number.split('/')[0]
        if len(prefix) < 2 or not prefix[:2].isdigit():
            return '-'
        yy = int(prefix[:2])
        current_yy = date.today().year % 100
        year = 2000 + yy if yy <= current_yy else 1900 + yy
        return str(year)

    rows = []
    for child in children:
        memberships = list(child.memberships.all())
        memberships_for_date = memberships
        if group_filters:
            memberships_for_date = [m for m in memberships if str(m.group_id) in group_filters]
        first_membership = memberships_for_date[0] if memberships_for_date else None
        parent_name = ''
        if child.parent:
            parent_name = f"{child.parent.first_name} {child.parent.last_name}".strip()
        groups_label = ', '.join(membership.group.name for membership in memberships) if memberships else ''
        rows.append({
            'child': child,
            'phone': child.parent.phone if child.parent else '',
            'birth_year': infer_birth_year(child),
            'joined_at': first_membership.registered_at.date() if first_membership else None,
            'memberships': memberships,
            'parent_name': parent_name,
            'groups_label': groups_label,
        })

    sort_map = {
        'name': lambda r: (
            (r['child'].last_name or '').lower(),
            (r['child'].first_name or '').lower(),
        ),
        'phone': lambda r: (r.get('phone') or ''),
        'birth_year': lambda r: int(r['birth_year']) if str(r['birth_year']).isdigit() else 0,
        'joined_at': lambda r: r.get('joined_at') or date.min,
        'vs': lambda r: int(r['child'].variable_symbol) if str(r['child'].variable_symbol).isdigit() else str(r['child'].variable_symbol or ''),
        'parent': lambda r: (r.get('parent_name') or '').lower(),
        'groups': lambda r: (r.get('groups_label') or '').lower(),
    }
    key_fn = sort_map.get(sort, sort_map['name'])
    rows.sort(key=key_fn, reverse=(direction == 'desc'))

    return rows


@role_required('admin')
def admin_children_export_xls(request):
    query = (request.GET.get('q') or '').strip()
    group_filters = [g for g in request.GET.getlist('groups') if g]
    sort = (request.GET.get('sort') or 'name').strip()
    direction = (request.GET.get('dir') or 'asc').strip().lower()
    rows = _children_rows(query=query, group_filters=group_filters, sort=sort, direction=direction)

    response = HttpResponse(content_type='application/vnd.ms-excel; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="deti_export.xls"'
    response.write('\ufeff')

    writer = csv.writer(response, delimiter='\t')
    writer.writerow([
        'Jméno',
        'Telefon rodiče',
        'Ročník',
        'Zapojení do skupiny',
        'VS',
        'Rodič',
        'Skupiny',
    ])

    for row in rows:
        groups = ', '.join(membership.group.name for membership in row['memberships']) if row['memberships'] else 'Bez skupiny'
        writer.writerow([
            f"{row['child'].first_name} {row['child'].last_name}",
            row['phone'] or '-',
            row['birth_year'],
            row['joined_at'].strftime('%d.%m.%Y') if row['joined_at'] else '-',
            row['child'].variable_symbol,
            f"{row['child'].parent.first_name} {row['child'].parent.last_name}",
            groups,
        ])

    return response


@role_required('admin')
def admin_contributions(request):
    current_q = (request.GET.get('q') or request.POST.get('q') or '').strip()
    current_sort = (request.GET.get('sort') or request.POST.get('sort') or 'name').strip() or 'name'
    current_dir = (request.GET.get('dir') or request.POST.get('dir') or 'asc').strip() or 'asc'
    current_group = (request.GET.get('group') or request.POST.get('group') or '').strip()

    def _redirect_current():
        url = reverse('admin_contributions')
        params = {}
        if current_q:
            params['q'] = current_q
        if current_sort:
            params['sort'] = current_sort
        if current_dir:
            params['dir'] = current_dir
        if current_group:
            params['group'] = current_group
        if params:
            url = f"{url}?{urlencode(params)}"
        return redirect(url)

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'authorize_proforma_selected':
            selected_ids = [item for item in request.POST.getlist('selected_membership_ids') if str(item).isdigit()]
            memberships = list(
                Membership.objects
                .filter(id__in=selected_ids, active=True)
                .select_related('group', 'group__sport', 'child', 'attendance_option')
                .order_by('id')
            )
            if not memberships:
                messages.warning(request, 'Nevybrali jste žádné děti pro autorizaci záloh.')
                return _redirect_current()

            open_membership_ids = set(
                ChildFinanceEntry.objects
                .filter(
                    membership_id__in=[m.id for m in memberships],
                    event_type=ChildFinanceEntry.TYPE_PROFORMA,
                    status=ChildFinanceEntry.STATUS_OPEN,
                )
                .values_list('membership_id', flat=True)
            )
            created_count = 0
            already_open_count = 0
            skipped_count = 0
            for membership in memberships:
                if membership.id in open_membership_ids:
                    already_open_count += 1
                    continue
                entry = _issue_membership_proforma(membership, created_by=request.user)
                if entry:
                    created_count += 1
                else:
                    skipped_count += 1
            messages.success(
                request,
                (
                    f'Autorizace vybraných dětí dokončena. '
                    f'Vystaveno: {created_count}, '
                    f'již existovalo: {already_open_count}, '
                    f'bez částky: {skipped_count}.'
                ),
            )
            return _redirect_current()

        if action == 'authorize_proforma_group':
            group_id = (request.POST.get('group_id') or '').strip()
            if not group_id.isdigit():
                messages.warning(request, 'Vyberte skupinu pro autorizaci záloh.')
                return _redirect_current()
            group = get_object_or_404(Group.objects.select_related('sport'), id=group_id)
            memberships = list(
                Membership.objects
                .filter(group=group, active=True)
                .select_related('group', 'group__sport', 'child', 'attendance_option')
                .order_by('id')
            )
            if not memberships:
                messages.warning(request, f'Skupina {group} nemá žádné aktivní členství.')
                return _redirect_current()

            open_membership_ids = set(
                ChildFinanceEntry.objects
                .filter(
                    membership_id__in=[m.id for m in memberships],
                    event_type=ChildFinanceEntry.TYPE_PROFORMA,
                    status=ChildFinanceEntry.STATUS_OPEN,
                )
                .values_list('membership_id', flat=True)
            )
            created_count = 0
            already_open_count = 0
            skipped_count = 0
            for membership in memberships:
                if membership.id in open_membership_ids:
                    already_open_count += 1
                    continue
                entry = _issue_membership_proforma(membership, created_by=request.user)
                if entry:
                    created_count += 1
                else:
                    skipped_count += 1
            messages.success(
                request,
                (
                    f'Autorizace skupiny {group} dokončena. '
                    f'Vystaveno: {created_count}, '
                    f'již existovalo: {already_open_count}, '
                    f'bez částky: {skipped_count}.'
                ),
            )
            return _redirect_current()

        membership = None
        membership_id = request.POST.get('membership_id')
        if membership_id:
            membership = get_object_or_404(
                Membership.objects.select_related('group', 'child', 'attendance_option', 'group__sport'),
                id=membership_id,
                active=True,
            )

        if action == 'update_membership' and membership:
            option_id = request.POST.get('attendance_option_id') or ''
            month_raw = (request.POST.get('billing_start_month') or '').strip()
            option = None
            if option_id:
                option = get_object_or_404(AttendanceOption, id=option_id, group=membership.group)
            membership.attendance_option = option
            if month_raw:
                try:
                    membership.billing_start_month = date.fromisoformat(f"{month_raw}-01")
                except ValueError:
                    messages.error(request, 'Neplatný měsíc zařazení.')
                    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                        return JsonResponse({'ok': False, 'error': 'Neplatný měsíc zařazení.'}, status=400)
                    return _redirect_current()
            else:
                membership.billing_start_month = None
            membership.save(update_fields=['attendance_option', 'billing_start_month'])
            cancelled = ChildFinanceEntry.objects.filter(
                membership=membership,
                event_type=ChildFinanceEntry.TYPE_PROFORMA,
                status=ChildFinanceEntry.STATUS_OPEN,
            ).update(status=ChildFinanceEntry.STATUS_CANCELLED)
            if cancelled:
                _create_finance_entry(
                    child=membership.child,
                    membership=membership,
                    event_type=ChildFinanceEntry.TYPE_INFO,
                    direction=ChildFinanceEntry.DIR_CREDIT,
                    status=ChildFinanceEntry.STATUS_CLOSED,
                    title='Změna členství – původní záloha stornována',
                    amount=Decimal('0.00'),
                    variable_symbol=membership.child.variable_symbol,
                    note='Po změně skupiny/varianty je nutné vystavit novou zálohu.',
                    created_by=request.user,
                    reference=_next_reference('STORNO'),
                )
            ChildArchiveLog.objects.create(
                child=membership.child,
                actor=request.user,
                event_type=ChildArchiveLog.EVENT_MEMBERSHIP,
                message=(
                    f"Změna členství v příspěvcích: {membership.group} | varianta "
                    f"{membership.attendance_option.name if membership.attendance_option else '-'}."
                ),
            )
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                base_price = membership.attendance_option.price_czk if membership.attendance_option else Decimal('0.00')
                selected_start = membership.billing_start_month
                if not selected_start and membership.registered_at:
                    selected_start = month_start(membership.registered_at.date())
                effective_start = normalize_start_month(
                    membership.group,
                    selected_start_month=selected_start,
                    fallback_date=date.today(),
                )
                payable_months = payable_months_count(
                    membership.group,
                    selected_start_month=effective_start,
                    fallback_date=date.today(),
                )
                due_amount = prorated_amount(base_price, payable_months)
                return JsonResponse({
                    'ok': True,
                    'membership_id': membership.id,
                    'base_price': str(base_price),
                    'due_amount': str(due_amount),
                    'payable_months': payable_months,
                    'effective_start': effective_start.strftime('%Y-%m') if effective_start else '',
                    'open_proforma_cancelled': cancelled > 0,
                })
            messages.success(request, 'Varianta a zařazení byly uložené.')
            return _redirect_current()

        if action == 'generate_proforma' and membership:
            entry = _issue_membership_proforma(membership, created_by=request.user)
            if entry:
                messages.success(request, f'Vygenerována záloha {entry.reference_code}.')
            else:
                messages.warning(request, 'Pro tuto položku není částka k úhradě.')
            return _redirect_current()

    sort = request.GET.get('sort', 'name')
    direction = request.GET.get('dir', 'asc')
    query = (request.GET.get('q') or '').strip()
    group_filter = (request.GET.get('group') or '').strip()
    desc = direction == 'desc'

    rows = (
        Membership.objects
        .filter(active=True)
        .select_related('child', 'child__parent', 'group', 'group__sport', 'attendance_option')
        .annotate(vs_num=Cast('child__variable_symbol', output_field=IntegerField()))
    )

    if query:
        rows = rows.filter(
            Q(child__first_name__icontains=query) |
            Q(child__last_name__icontains=query) |
            Q(child__variable_symbol__icontains=query)
        )
    if group_filter.isdigit():
        rows = rows.filter(group_id=int(group_filter))
    else:
        group_filter = ''

    sort_map = {
        'name': ['child__last_name', 'child__first_name'],
        'phone': ['child__parent__phone'],
        'vs': ['vs_num', 'child__variable_symbol'],
        'birth_number': ['child__birth_number'],
        'group': ['group__sport__name', 'group__name'],
        'option': ['attendance_option__name'],
        'amount': ['attendance_option__price_czk'],
    }
    order_fields = sort_map.get(sort, sort_map['name'])
    if desc:
        order_fields = [f'-{field}' for field in order_fields]
    rows = rows.order_by(*order_fields)

    rows = list(rows)
    options_by_group = {
        group.id: list(group.attendance_options.all().order_by('frequency_per_week', 'name'))
        for group in Group.objects.prefetch_related('attendance_options')
    }
    payment_counter = build_payment_counter(ReceivedPayment.objects.all().only('variable_symbol', 'amount_czk'))
    total_due = 0
    for row in rows:
        base_price = row.attendance_option.price_czk if row.attendance_option else 0
        selected_start = row.billing_start_month
        if not selected_start and row.registered_at:
            selected_start = month_start(row.registered_at.date())
        effective_start = normalize_start_month(
            row.group,
            selected_start_month=selected_start,
            fallback_date=date.today(),
        )
        payable_months = payable_months_count(
            row.group,
            selected_start_month=effective_start,
            fallback_date=date.today(),
        )
        due_amount = prorated_amount(base_price, payable_months)
        row.effective_start = effective_start
        row.payable_months = payable_months
        row.base_price = base_price
        row.due_amount = due_amount
        finance_entries = list(
            ChildFinanceEntry.objects.filter(membership=row).order_by('-id')[:20]
        )
        open_proforma = next((e for e in finance_entries if e.event_type == ChildFinanceEntry.TYPE_PROFORMA and e.status == ChildFinanceEntry.STATUS_OPEN), None)
        has_invoice = any(e.event_type == ChildFinanceEntry.TYPE_INVOICE and e.status == ChildFinanceEntry.STATUS_CLOSED for e in finance_entries)
        paid_by_old_matching = consume_matching_payment(payment_counter, row.child.variable_symbol, due_amount)
        row.open_proforma = open_proforma
        row.has_invoice = has_invoice
        row.paid = has_invoice or paid_by_old_matching
        row.group_options = options_by_group.get(row.group_id, [])
        row.month_choices = [
            m.strftime('%Y-%m')
            for m in _allowed_start_months(row.group)
        ]
        total_due += due_amount

    return render(request, 'admin/contributions.html', {
        'rows': rows,
        'sort': sort,
        'direction': direction,
        'query': query,
        'group_filter': group_filter,
        'full_period_months': FULL_PERIOD_MONTHS,
        'total_due': total_due,
        'groups_nav': _groups_nav(),
        'admin_wide_content': True,
    })


@role_required('admin')
def admin_received_payments(request):
    form = ReceivedPaymentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        payment = form.save()
        matched_entry = _match_payment_to_open_proforma(payment, created_by=request.user)
        if not matched_entry:
            child = Child.objects.filter(variable_symbol=str(payment.variable_symbol)).first()
            if child:
                _create_finance_entry(
                    child=child,
                    event_type=ChildFinanceEntry.TYPE_PAYMENT,
                    direction=ChildFinanceEntry.DIR_CREDIT,
                    status=ChildFinanceEntry.STATUS_CLOSED,
                    title='Příchozí platba (bez párování)',
                    amount=payment.amount_czk,
                    variable_symbol=payment.variable_symbol,
                    note=f"{payment.sender_name or ''} {payment.note or ''}".strip(),
                    created_by=request.user,
                    reference=_next_reference('PLT'),
                )
        messages.success(
            request,
            f'Přijata platba VS {payment.variable_symbol} ve výši {payment.amount_czk} Kč.',
        )
        return redirect('admin_received_payments')

    payments = ReceivedPayment.objects.order_by('-received_date', '-id')
    return render(request, 'admin/received_payments.html', {
        'form': form,
        'payments': payments,
        'groups_nav': _groups_nav(),
        'admin_wide_content': True,
    })


@role_required('admin')
def admin_documents(request):
    form = ClubDocumentForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        document = form.save(commit=False)
        document.uploaded_by = request.user
        document.save()
        messages.success(request, 'Dokument byl uložen.')
        return redirect('admin_documents')

    documents = ClubDocument.objects.select_related('uploaded_by').order_by('-uploaded_at', '-id')
    return render(request, 'admin/documents.html', {
        'form': form,
        'documents': documents,
        'groups_nav': _groups_nav(),
        'admin_wide_content': True,
    })
