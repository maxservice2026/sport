from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import render, get_object_or_404, redirect

from users.utils import role_required
from clubs.models import Group, Child, Membership
from .models import TrainingSession, Attendance, TrainerAttendance

DAY_TO_WEEKDAY = {
    'Po': 0,
    'Út': 1,
    'St': 2,
    'Čt': 3,
    'Pá': 4,
    'So': 5,
    'Ne': 6,
}

WEEKDAY_LABELS = {
    0: 'Pondělí',
    1: 'Úterý',
    2: 'Středa',
    3: 'Čtvrtek',
    4: 'Pátek',
    5: 'Sobota',
    6: 'Neděle',
}


def _birth_month_day_from_birth_number(birth_number):
    if not birth_number:
        return None
    raw = str(birth_number).strip().split('/')[0]
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if len(digits) < 6:
        return None
    mm = int(digits[2:4])
    dd = int(digits[4:6])

    # CZ birth-number month offsets used for women/capacity extensions.
    if mm > 70:
        mm -= 70
    elif mm > 50:
        mm -= 50
    elif mm > 20:
        mm -= 20

    try:
        date(2000, mm, dd)
    except ValueError:
        return None
    return mm, dd


def _is_child_birthday(child, session_date):
    if not session_date:
        return False
    month_day = _birth_month_day_from_birth_number(child.birth_number)
    if not month_day:
        return False
    return month_day == (session_date.month, session_date.day)


def _attendance_percentage(child, group, dates_up_to):
    total = len(dates_up_to)
    if total == 0:
        return 0
    present = Attendance.objects.filter(
        session__group=group,
        session__date__in=dates_up_to,
        child=child,
        present=True,
    ).count()
    return int((present / total) * 100)


def _is_training_day(group, session_date):
    if not group.training_days:
        return True
    weekdays = {DAY_TO_WEEKDAY.get(day) for day in group.training_days}
    return session_date.weekday() in weekdays


def _is_within_range(group, session_date):
    if group.start_date and session_date < group.start_date:
        return False
    if group.end_date and session_date > group.end_date:
        return False
    return True


def _training_dates(group, max_date=None):
    if not group.start_date or not group.end_date:
        return []
    if group.start_date > group.end_date:
        return []

    if group.training_days:
        allowed = {DAY_TO_WEEKDAY.get(day) for day in group.training_days}
    else:
        allowed = set(range(7))

    limit_end = group.end_date
    if max_date and max_date < limit_end:
        limit_end = max_date
    if limit_end < group.start_date:
        return []

    dates = []
    cur = group.start_date
    while cur <= limit_end:
        if cur.weekday() in allowed:
            dates.append(cur)
        cur += timedelta(days=1)
    return dates


def _session_dates(group, max_date=None):
    scheduled_dates = set(_training_dates(group, max_date=max_date))
    sessions_qs = TrainingSession.objects.filter(group=group)
    if max_date:
        sessions_qs = sessions_qs.filter(date__lte=max_date)
    cancelled_dates = set(
        sessions_qs.filter(is_cancelled=True).values_list('date', flat=True)
    )
    extra_dates = set(
        sessions_qs.filter(is_extra=True, is_cancelled=False).values_list('date', flat=True)
    )
    return sorted((scheduled_dates - cancelled_dates) | extra_dates)


def _select_session_date(group, requested, max_date=None):
    dates = _session_dates(group, max_date=max_date)
    if not dates:
        return requested or date.today(), [], False, dates

    if requested and requested in dates:
        selected = requested
    else:
        today = date.today()
        selected = today if today in dates else dates[0]

    options = [
        {
            'value': d.isoformat(),
            'label': f"{d.strftime('%d.%m.%Y')} - {WEEKDAY_LABELS[d.weekday()]}",
        }
        for d in dates
    ]
    return selected, options, True, dates


def _trainer_attendance_percentage(trainer, group, dates_up_to):
    total = len(dates_up_to)
    if total == 0:
        return 0
    present = TrainerAttendance.objects.filter(
        session__group=group,
        session__date__in=dates_up_to,
        trainer=trainer,
        present=True,
    ).count()
    return int((present / total) * 100)


def _trainer_tiles(group, session, dates_up_to, only_trainer_id=None):
    trainers_qs = group.trainers.order_by('last_name', 'first_name')
    if only_trainer_id is not None:
        trainers_qs = trainers_qs.filter(id=only_trainer_id)
    trainers = list(trainers_qs)
    if not trainers:
        return []

    present_ids = set()
    if session:
        present_ids = set(
            TrainerAttendance.objects.filter(
                session=session,
                trainer_id__in=[trainer.id for trainer in trainers],
                present=True,
            ).values_list('trainer_id', flat=True)
        )

    tiles = []
    for trainer in trainers:
        label = f"{trainer.first_name} {trainer.last_name}".strip() or trainer.email
        tiles.append({
            'trainer': trainer,
            'label': label,
            'present': trainer.id in present_ids,
            'percent': _trainer_attendance_percentage(trainer, group, dates_up_to),
        })
    return tiles


def _attendance_context(request, group, requested_date, max_date=None, trainer_filter_id=None):
    session_date, session_options, has_schedule, training_dates = _select_session_date(
        group,
        requested_date,
        max_date=max_date,
    )
    can_mark = has_schedule
    session = None
    if can_mark:
        session = TrainingSession.objects.filter(group=group, date=session_date, is_cancelled=False).first()
    else:
        if request:
            messages.warning(request, 'Skupina nemá nastavené období nebo tréninkové dny.')

    dates_up_to = [d for d in training_dates if d <= session_date] if training_dates else []
    memberships = Membership.objects.filter(group=group, active=True).select_related('child', 'child__parent')
    tiles = []
    for membership in memberships:
        child = membership.child
        is_present = False
        if session:
            is_present = Attendance.objects.filter(session=session, child=child, present=True).exists()
        tiles.append({
            'child': child,
            'present': is_present,
            'percent': _attendance_percentage(child, group, dates_up_to),
            'parent_phone': getattr(child.parent, 'phone', '') if hasattr(child, 'parent') else '',
            'is_birthday': _is_child_birthday(child, session_date),
        })

    trainer_tiles = _trainer_tiles(group, session, dates_up_to, only_trainer_id=trainer_filter_id)
    return session_date, session_options, can_mark, session, tiles, trainer_tiles


def _ensure_active_session(group, session_date):
    session, _ = TrainingSession.objects.get_or_create(
        group=group,
        date=session_date,
        defaults={
            'is_extra': not _is_training_day(group, session_date),
            'is_cancelled': False,
        },
    )
    updates = []
    if session.is_cancelled:
        session.is_cancelled = False
        updates.append('is_cancelled')
    should_be_extra = not _is_training_day(group, session_date)
    if session.is_extra != should_be_extra:
        session.is_extra = should_be_extra
        updates.append('is_extra')
    if updates:
        session.save(update_fields=updates)
    return session


def _cancel_session_day(group, session_date):
    session, _ = TrainingSession.objects.get_or_create(
        group=group,
        date=session_date,
        defaults={
            'is_extra': not _is_training_day(group, session_date),
            'is_cancelled': True,
        },
    )
    updates = []
    if not session.is_cancelled:
        session.is_cancelled = True
        updates.append('is_cancelled')
    should_be_extra = not _is_training_day(group, session_date)
    if session.is_extra != should_be_extra:
        session.is_extra = should_be_extra
        updates.append('is_extra')
    if updates:
        session.save(update_fields=updates)
    Attendance.objects.filter(session=session).delete()
    TrainerAttendance.objects.filter(session=session).delete()
    return session


@role_required('trainer')
def trainer_attendance(request, group_id):
    allow_extra = request.GET.get('extra') == '1' or request.POST.get('extra') == '1'
    group = get_object_or_404(Group, id=group_id)
    is_assigned = group.trainers.filter(id=request.user.id).exists()
    if not is_assigned and not allow_extra:
        messages.error(request, 'Tato skupina není ve vašem přiřazení. Použijte mimořádný vstup.')
        return redirect('trainer_dashboard')
    requested = request.GET.get('date') or request.POST.get('date')
    requested = date.fromisoformat(requested) if requested else None
    extra_suffix = '&extra=1' if not is_assigned else ''

    session_date, session_options, can_mark, session, tiles, trainer_tiles = _attendance_context(
        request,
        group,
        requested,
        max_date=date.today(),
        trainer_filter_id=request.user.id,
    )
    if session_date > date.today():
        can_mark = False
        session = None
        messages.warning(request, 'Docházku nelze zadávat do budoucnosti.')

    if request.method == 'POST':
        if not can_mark:
            return redirect(request.path + f"?date={session_date}{extra_suffix}")
        if not session:
            session = _ensure_active_session(group, session_date)
        trainer_id = request.POST.get('trainer_id')
        if trainer_id:
            trainer_record, created = TrainerAttendance.objects.get_or_create(
                session=session,
                trainer_id=request.user.id,
                defaults={'extra_access': not is_assigned},
            )
            if not created:
                trainer_record.present = not trainer_record.present
                trainer_record.extra_access = not is_assigned
                trainer_record.save(update_fields=['present', 'extra_access', 'recorded_at'])
            return redirect(request.path + f"?date={session_date}{extra_suffix}")

        child_id = request.POST.get('child_id')
        if child_id:
            child = get_object_or_404(Child, id=child_id)
            record, created = Attendance.objects.get_or_create(session=session, child=child)
            if not created:
                record.present = not record.present
                record.save(update_fields=['present', 'recorded_at'])
        return redirect(request.path + f"?date={session_date}{extra_suffix}")

    return render(request, 'trainer/attendance.html', {
        'group': group,
        'session_date': session_date,
        'session_options': session_options,
        'can_mark': can_mark,
        'tiles': tiles,
        'trainer_tiles': trainer_tiles,
        'extra_mode': not is_assigned,
    })


@role_required('admin')
def admin_attendance(request):
    groups = Group.objects.select_related('sport').order_by('sport__name', 'name')
    group_id = request.GET.get('group') or request.POST.get('group')
    group = None
    if group_id:
        group = get_object_or_404(Group, id=group_id)

    if not group:
        return render(request, 'admin/attendance.html', {
            'groups': groups,
            'group': None,
            'session_options': [],
            'tiles': [],
            'can_mark': False,
            'session_date': None,
            'groups_nav': groups,
        })

    requested = request.GET.get('date') or request.POST.get('date')
    requested = date.fromisoformat(requested) if requested else None

    session_date, session_options, can_mark, session, tiles, trainer_tiles = _attendance_context(
        request,
        group,
        requested,
        max_date=date.today(),
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_training_day':
            add_raw = (request.POST.get('add_date') or '').strip()
            if not add_raw:
                messages.error(request, 'Vyberte datum pro přidání tréninku.')
                return redirect(request.path + f"?group={group.id}&date={session_date}")
            try:
                add_date = date.fromisoformat(add_raw)
            except ValueError:
                messages.error(request, 'Neplatné datum tréninku.')
                return redirect(request.path + f"?group={group.id}&date={session_date}")
            if add_date > date.today():
                messages.error(request, 'Trénink nelze přidat do budoucnosti.')
                return redirect(request.path + f"?group={group.id}&date={session_date}")
            if not _is_within_range(group, add_date):
                messages.error(request, 'Datum není v období této skupiny.')
                return redirect(request.path + f"?group={group.id}&date={session_date}")
            _ensure_active_session(group, add_date)
            messages.success(request, f'Tréninkový den {add_date.strftime("%d.%m.%Y")} byl přidán.')
            return redirect(request.path + f"?group={group.id}&date={add_date.isoformat()}")

        if action == 'delete_training_day':
            if not can_mark:
                messages.error(request, 'Skupina nemá žádné dostupné datum pro smazání.')
                return redirect(request.path + f"?group={group.id}")
            _cancel_session_day(group, session_date)
            messages.success(request, f'Tréninkový den {session_date.strftime("%d.%m.%Y")} byl smazán.')
            return redirect(request.path + f"?group={group.id}")

        if not can_mark:
            return redirect(request.path + f"?group={group.id}&date={session_date}")
        if not session:
            session = _ensure_active_session(group, session_date)
        trainer_id = request.POST.get('trainer_id')
        if trainer_id:
            trainer = get_object_or_404(group.trainers, id=trainer_id)
            trainer_record, created = TrainerAttendance.objects.get_or_create(
                session=session,
                trainer=trainer,
            )
            if not created:
                trainer_record.present = not trainer_record.present
                trainer_record.save(update_fields=['present', 'recorded_at'])
            return redirect(request.path + f"?group={group.id}&date={session_date}")

        child_id = request.POST.get('child_id')
        if child_id:
            child = get_object_or_404(Child, id=child_id)
            record, created = Attendance.objects.get_or_create(session=session, child=child)
            if not created:
                record.present = not record.present
                record.save(update_fields=['present', 'recorded_at'])
        return redirect(request.path + f"?group={group.id}&date={session_date}")

    return render(request, 'admin/attendance.html', {
        'groups': groups,
        'group': group,
        'session_date': session_date,
        'session_options': session_options,
        'can_mark': can_mark,
        'tiles': tiles,
        'trainer_tiles': trainer_tiles,
        'groups_nav': groups,
    })
