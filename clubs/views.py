import csv
from datetime import date, timedelta, datetime

from django.contrib import messages
from django.contrib.auth import login
from django.db import transaction
from django.forms import inlineformset_factory
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, Count, IntegerField, Prefetch
from django.db.models.functions import Cast
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone

from users.utils import role_required
from .forms import RegistrationForm, GroupAdminForm, AttendanceOptionForm, ChildEditForm, AdminMembershipAddForm, ReceivedPaymentForm
from .models import Group, AttendanceOption, Membership, Child, ReceivedPayment
from .pricing import FULL_PERIOD_MONTHS, group_month_starts, month_start, normalize_start_month, payable_months_count, prorated_amount
from .payments import build_payment_counter, consume_matching_payment
from attendance.models import Attendance


DAY_TO_WEEKDAY = {
    'Po': 0,
    'Út': 1,
    'St': 2,
    'Čt': 3,
    'Pá': 4,
    'So': 5,
    'Ne': 6,
}


def public_register(request):
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        parent, child, membership, created_parent, created_child, membership_created = form.save()
        login(request, parent)
        if not created_child and membership_created:
            messages.info(request, 'Dítě již existovalo a bylo přiřazeno do nové skupiny.')
        elif not membership_created:
            messages.warning(request, 'Dítě je již v této skupině. Vyberte jinou skupinu.')
        return redirect('parent_dashboard')
    return render(request, 'public/register.html', {'form': form})


def attendance_options_api(request):
    group_id = request.GET.get('group_id')
    if not group_id:
        return JsonResponse({'options': [], 'months': []})
    group = Group.objects.filter(id=group_id).first()
    options = AttendanceOption.objects.filter(group_id=group_id).values('id', 'name')
    months = []
    if group:
        months = [
            {'value': m.strftime('%Y-%m'), 'label': m.strftime('%m/%Y')}
            for m in group_month_starts(group)
        ]
    return JsonResponse({'options': list(options), 'months': months})


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


@role_required('admin')
def admin_group_list(request):
    groups = Group.objects.select_related('sport').prefetch_related('trainers').order_by('sport__name', 'name')
    return render(request, 'admin/groups_list.html', {'groups': groups, 'groups_nav': _groups_nav()})


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
            redirect_url += f'?q={final_query}'
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
    memberships = Membership.objects.filter(child=child).select_related('group', 'attendance_option')
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
            messages.success(request, 'Datum zařazení do skupiny bylo uloženo.')
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
            messages.success(request, 'Dítě bylo přiřazeno do skupiny.')
            return redirect('admin_child_edit', child_id=child.id)
        elif 'save_child' in request.POST and form.is_valid():
            form.save()
            messages.success(request, 'Údaje dítěte byly uloženy.')
            return redirect('admin_child_edit', child_id=child.id)
    return render(request, 'admin/child_form.html', {
        'child': child,
        'form': form,
        'membership_form': membership_form,
        'memberships': memberships,
        'groups_nav': _groups_nav(),
    })


@role_required('admin')
def admin_children_list(request):
    query = (request.GET.get('q') or '').strip()
    group_filter = request.GET.get('group') or ''
    rows = _children_rows(query=query, group_filter=group_filter)
    return render(request, 'admin/children_list.html', {
        'rows': rows,
        'query': query,
        'group_filter': group_filter,
        'groups': Group.objects.select_related('sport').order_by('sport__name', 'name'),
        'groups_nav': _groups_nav(),
    })


def _children_rows(query='', group_filter=''):
    query = (query or '').strip()
    group_filter = str(group_filter or '').strip()

    memberships_prefetch = Prefetch(
        'memberships',
        queryset=Membership.objects.select_related('group').order_by('registered_at', 'id'),
    )
    children = Child.objects.select_related('parent').prefetch_related(memberships_prefetch)
    if group_filter:
        children = children.filter(memberships__group_id=group_filter)
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
        if group_filter:
            memberships_for_date = [m for m in memberships if str(m.group_id) == str(group_filter)]
        first_membership = memberships_for_date[0] if memberships_for_date else None
        rows.append({
            'child': child,
            'phone': child.parent.phone if child.parent else '',
            'birth_year': infer_birth_year(child),
            'joined_at': first_membership.registered_at.date() if first_membership else None,
            'memberships': memberships,
        })

    return rows


@role_required('admin')
def admin_children_export_xls(request):
    query = (request.GET.get('q') or '').strip()
    group_filter = request.GET.get('group') or ''
    rows = _children_rows(query=query, group_filter=group_filter)

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
    sort = request.GET.get('sort', 'name')
    direction = request.GET.get('dir', 'asc')
    query = (request.GET.get('q') or '').strip()
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
        row.paid = consume_matching_payment(payment_counter, row.child.variable_symbol, due_amount)
        total_due += due_amount

    return render(request, 'admin/contributions.html', {
        'rows': rows,
        'sort': sort,
        'direction': direction,
        'query': query,
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
