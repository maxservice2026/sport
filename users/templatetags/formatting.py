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
