"""Exact settlement arithmetic for prospective net-entered payroll payments.

This allocates an already calculated payroll balance; it never computes new
statutory tax rates or reconstructs undocumented historical withholding.
Budget remittances are deliberately not inputs.
"""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal('0.01')


class PaidIncomeError(ValueError):
    pass


def money(value):
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise PaidIncomeError('Exact monetary value required')
    try:
        result = Decimal(value)
        if not result.is_finite() or result != result.quantize(CENT):
            raise InvalidOperation
        return result.quantize(CENT)
    except (InvalidOperation, ValueError):
        raise PaidIncomeError('Invalid money') from None


def allocate_remaining_net(*, gross, pit, levy, previous, cash, non_tax_withheld='0.00'):
    """Allocate a NEW payment against the remaining frozen settlement pool.

    Prior allocations must come from verified advances or immutable payment
    records. Applying this policy retrospectively is not source verification.
    The last payment consumes exact residuals, including tax refunds from an
    advance; earlier payments use rounded shares of the remaining pool.
    """
    remaining = {k: money(v) for k, v in dict(gross=gross, pit=pit, levy=levy).items()}
    if any(v < 0 for v in remaining.values()):
        raise PaidIncomeError('Nonnegative monthly source totals required')
    if type(previous) not in (list, tuple):
        raise PaidIncomeError('Previous source allocations required')
    for item in previous:
        if type(item) is not dict or set(item) != {'gross', 'pit', 'levy', 'net'}:
            raise PaidIncomeError('Invalid previous allocation')
        part = {k: money(v) for k, v in item.items()}
        if part['gross'] < 0 or part['net'] <= 0 or part['gross'] != part['net'] + part['pit'] + part['levy']:
            raise PaidIncomeError('Previous allocation does not reconcile')
        for key in remaining:
            remaining[key] -= part[key]
    net_left = remaining['gross'] - remaining['pit'] - remaining['levy']
    cash, withheld = money(cash), money(non_tax_withheld)
    net = cash + withheld
    if cash <= 0 or withheld < 0 or net > net_left or remaining['gross'] < 0:
        raise PaidIncomeError('Payment exceeds remaining payroll balance')
    taxes = {k: (remaining[k] if net == net_left else
                 (remaining[k] * net / net_left).quantize(CENT, rounding=ROUND_HALF_UP))
             for k in ('pit', 'levy')}
    paid_gross = net + taxes['pit'] + taxes['levy']
    if paid_gross < 0 or paid_gross > remaining['gross']:
        raise PaidIncomeError('Requested net has no valid allocation at cent precision')
    return {k: format(v, '.2f') for k, v in
            dict(gross=paid_gross, pit=taxes['pit'], levy=taxes['levy'], net=net).items()}


def paid_income_month(*, accrual_month, payment_date, salary, contractual_due_date=None):
    """Apply the salary-only next-month exception to a verified payment.

    The caller supplies the actual dated contractual deadline after calendar
    adjustment. Missing deadlines never silently classify next-month salary.
    """
    try:
        start = date.fromisoformat(accrual_month + '-01')
    except (TypeError, ValueError):
        raise PaidIncomeError('Canonical accrual month required') from None
    if start.strftime('%Y-%m') != accrual_month or type(payment_date) is not date or type(salary) is not bool:
        raise PaidIncomeError('Invalid payment period inputs')
    following = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    actual_month = payment_date.strftime('%Y-%m')
    if not salary or payment_date < following:
        return actual_month
    if (payment_date.year, payment_date.month) != (following.year, following.month):
        return actual_month
    if type(contractual_due_date) is not date or not start <= contractual_due_date <= following.replace(day=7):
        raise PaidIncomeError('Verified next-month salary deadline required')
    return accrual_month if payment_date <= contractual_due_date else actual_month
