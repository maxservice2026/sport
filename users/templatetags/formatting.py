from decimal import Decimal, InvalidOperation
from decimal import ROUND_HALF_UP

from django import template


register = template.Library()


@register.filter
def czk_int(value):
    try:
        number = Decimal(value or 0)
    except (InvalidOperation, TypeError, ValueError):
        return '0'
    rounded = number.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return f"{int(rounded):,}".replace(',', ' ')


@register.filter
def czk_amount(value):
    try:
        number = Decimal(value or 0)
    except (InvalidOperation, TypeError, ValueError):
        return '0,00'
    rounded = number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    int_part = int(abs(rounded))
    frac_part = int((abs(rounded) - int_part) * 100)
    sign = '-' if rounded < 0 else ''
    return f"{sign}{int_part:,}".replace(',', ' ') + f",{frac_part:02d}"


@register.filter
def spd_amount(value):
    try:
        number = Decimal(value or 0)
    except (InvalidOperation, TypeError, ValueError):
        return '0.00'
    rounded = number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return f"{rounded:.2f}"
