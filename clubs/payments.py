from collections import Counter
from decimal import Decimal, ROUND_HALF_UP


def normalize_vs(value):
    return str(value or '').strip()


def normalize_amount(value):
    amount = Decimal(value or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return f"{amount:.2f}"


def build_payment_counter(payments_iterable):
    counter = Counter()
    for payment in payments_iterable:
        key = (normalize_vs(payment.variable_symbol), normalize_amount(payment.amount_czk))
        counter[key] += 1
    return counter


def consume_matching_payment(counter, variable_symbol, amount_czk):
    key = (normalize_vs(variable_symbol), normalize_amount(amount_czk))
    if counter.get(key, 0) > 0:
        counter[key] -= 1
        return True
    return False
