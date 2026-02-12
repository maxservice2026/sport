from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def czk_int(value):
    try:
        number = Decimal(value or 0)
    except (InvalidOperation, TypeError, ValueError):
        return '0'
    return f"{int(number):,}".replace(',', ' ')

