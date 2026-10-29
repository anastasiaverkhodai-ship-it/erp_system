"""Deterministic leave calendar changes; no mutation or payment authorization.

Retained dates preserve the original daily allocation, including rounding.
Transferred days are a new leave entitlement calculation at their new start,
not a silent reuse of the old average daily wage.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP


class LeaveChangeError(ValueError):
    pass


@dataclass(frozen=True)
class LeaveRange:
    start: date
    end: date

    def dates(self):
        if self.end < self.start:
            raise LeaveChangeError('Leave range ends before it starts')
        return tuple(self.start + timedelta(days=i) for i in range((self.end-self.start).days+1))


@dataclass(frozen=True)
class LeaveChangePlan:
    used_dates: tuple[date, ...]
    released_dates: tuple[date, ...]
    transferred_dates: tuple[date, ...]
    retained_amount: Decimal
    reduction_amount: Decimal
    monthly_amounts: tuple[tuple[date, Decimal], ...]


RECALL_GROUNDS = frozenset({'natural_disaster', 'industrial_accident',
    'accident_prevention', 'downtime_prevention', 'property_protection', 'other_statutory'})


def _dates(ranges):
    result = []
    for segment in ranges:
        result.extend(segment.dates())
    if len(result) != len(set(result)):
        raise LeaveChangeError('Leave segments overlap')
    return tuple(sorted(result))


def plan_leave_change(*, original: LeaveRange, used: tuple[LeaveRange, ...],
                      transferred: tuple[LeaveRange, ...], original_amount: Decimal,
                      leave_kind: str, operation: str, effective_date: date,
                      order_reference: str, consent_reference: str,
                      recall_ground: str | None = None,
                      legal_reference: str | None = None):
    if leave_kind not in {'annual', 'additional', 'social'}:
        raise LeaveChangeError('Unsupported paid leave kind')
    if operation not in {'partial', 'recall', 'transfer'}:
        raise LeaveChangeError('Unsupported leave operation')
    if not order_reference.strip() or not consent_reference.strip():
        raise LeaveChangeError('Documented order and employee consent are required')
    if operation == 'recall':
        if leave_kind not in {'annual', 'additional'}:
            raise LeaveChangeError('Recall is restricted to annual leave')
        if recall_ground not in RECALL_GROUNDS:
            raise LeaveChangeError('A statutory recall ground is required')
        if recall_ground == 'other_statutory' and not (legal_reference or '').strip():
            raise LeaveChangeError('Other statutory recall requires its exact legal source')
        if not original.start <= effective_date <= original.end:
            raise LeaveChangeError('Recall date must fall within the original leave')
    amount = Decimal(original_amount)
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal('.01')):
        raise LeaveChangeError('Original leave amount must be nonnegative money')
    original_dates = original.dates()
    used_dates = _dates(used)
    transferred_dates = _dates(transferred)
    if not set(used_dates) <= set(original_dates):
        raise LeaveChangeError('Used dates must belong to the original leave')
    released = tuple(day for day in original_dates if day not in used_dates)
    if not released:
        raise LeaveChangeError('The change releases no leave days')
    if len(transferred_dates) > len(released):
        raise LeaveChangeError('Transferred days exceed released rights')
    if set(used_dates) & set(transferred_dates):
        raise LeaveChangeError('Transferred leave overlaps retained leave')
    if any(day < effective_date for day in transferred_dates):
        raise LeaveChangeError('Transferred leave cannot precede its documented change')
    if operation == 'recall' and any(day >= effective_date for day in used_dates):
        raise LeaveChangeError('Recalled leave must end before return to work')
    if operation == 'transfer' and len(transferred_dates) != len(released):
        raise LeaveChangeError('A transfer must account for every transferred day')
    # Differences of cumulative cents preserve the original entitlement exactly.
    def cumulative(n):
        return (amount*n/len(original_dates)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    daily = {day: cumulative(i+1)-cumulative(i) for i,day in enumerate(original_dates)}
    monthly = {}
    for day in used_dates:
        month = day.replace(day=1)
        monthly[month] = monthly.get(month, Decimal('0.00')) + daily[day]
    retained = sum(monthly.values(), Decimal('0.00'))
    return LeaveChangePlan(used_dates, released, transferred_dates, retained,
        amount-retained, tuple(sorted(monthly.items())))
