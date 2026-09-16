"""Versioned ordinary domestic PN credit policy, verified 2026-09-16.

Historical PN before 2023-08-01, cash-method and special documents need a
separate policy. Suspension intervals are attested, disjoint, half-open dates.
The result governs the tax period; registration receipt remains operator evidence.
"""
from calendar import monthrange
from datetime import date, timedelta
from app.schemas.input_vat_credit_claim import InputVatCreditClaimCreate, InputVatCreditEligibility

POLICY_VERSION = 'ua-domestic-pn-first-event-2026-09-16'
POLICY_FROM = date(2023, 8, 1)
POLICY_VERIFIED_THROUGH = date(2026, 9, 16)


def month_start(day):
    return day.replace(day=1)


def month_end(day):
    return day.replace(day=monthrange(day.year, day.month)[1])


def assess_input_vat_credit(data: InputVatCreditClaimCreate, *, as_of_date: date) -> InputVatCreditEligibility:
    values = dict(policy_version=POLICY_VERSION)
    def deny(reason):
        return InputVatCreditEligibility(eligible=False, reason=reason, **values)
    if not POLICY_FROM <= data.invoice_date <= POLICY_VERIFIED_THROUGH:
        return deny('invoice_date_requires_another_policy_version')
    if data.invoice_date > as_of_date or data.claim_period > month_start(as_of_date):
        return deny('future_invoice_or_claim_period')
    if data.registration_status != 'registered':
        return deny('registration_not_confirmed')
    registered = data.registered_on
    if registered < data.invoice_date or registered > as_of_date:
        return deny('invalid_registration_date')
    # Subsection 2 paragraph 89: next month day 5 / day 18. Applicability
    # is deliberately bounded by the verified invoice-date policy window.
    next_month = month_end(data.invoice_date) + timedelta(days=1)
    deadline = next_month.replace(day=5 if data.invoice_date.day <= 15 else 18)
    timely = registered <= deadline
    first_period = month_start(data.invoice_date if timely else registered)
    values.update(registration_deadline=deadline, timely_registration=timely, first_eligible_period=first_period)
    expiry = data.invoice_date + timedelta(days=365)
    previous_end = data.invoice_date
    for interval in sorted(data.suspensions, key=lambda item: item.suspended_on):
        if interval.suspended_on < previous_end or interval.resumed_on > registered:
            return deny('invalid_or_overlapping_suspension')
        if interval.suspended_on > expiry:
            return deny('suspension_cannot_revive_expired_credit')
        expiry += interval.resumed_on - interval.suspended_on
        previous_end = interval.resumed_on
    values['expires_on'] = expiry
    if registered > expiry:
        return deny('registration_after_credit_deadline')
    if data.claim_period < first_period:
        return deny('claim_period_before_eligibility')
    # A declaration is monthly: the last admissible month contains expiry.
    if data.claim_period > month_start(expiry):
        return deny('claim_period_after_credit_deadline')
    available = max(data.claim_period, data.invoice_date if timely else registered)
    values['credit_available_date'] = available
    return InputVatCreditEligibility(eligible=True, reason='eligible_subject_to_economic_and_registration_checks', **values)
