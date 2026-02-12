from datetime import date
from decimal import Decimal, ROUND_HALF_UP


FULL_PERIOD_MONTHS = 5


def month_start(value):
    return date(value.year, value.month, 1)


def add_months(value, months):
    month_index = (value.year * 12 + value.month - 1) + months
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def group_month_starts(group):
    if not group.start_date or not group.end_date:
        return []
    if group.start_date > group.end_date:
        return []

    start = month_start(group.start_date)
    end = month_start(group.end_date)
    result = []
    current = start
    while current <= end:
        result.append(current)
        current = add_months(current, 1)
    return result


def normalize_start_month(group, selected_start_month=None, fallback_date=None):
    months = group_month_starts(group)
    if not months:
        base = selected_start_month or fallback_date or date.today()
        return month_start(base)

    if selected_start_month:
        selected = month_start(selected_start_month)
    else:
        base = fallback_date or months[0]
        selected = month_start(base)

    if selected < months[0]:
        return months[0]
    if selected > months[-1]:
        return months[-1]
    return selected


def payable_months_count(group, selected_start_month=None, fallback_date=None):
    months = group_month_starts(group)
    if not months:
        return FULL_PERIOD_MONTHS

    start = normalize_start_month(group, selected_start_month=selected_start_month, fallback_date=fallback_date)
    count = len([m for m in months if m >= start])
    return max(1, min(FULL_PERIOD_MONTHS, count))


def prorated_amount(full_price, payable_months):
    if not full_price:
        return Decimal('0.00')
    amount = (Decimal(full_price) * Decimal(payable_months)) / Decimal(FULL_PERIOD_MONTHS)
    return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
