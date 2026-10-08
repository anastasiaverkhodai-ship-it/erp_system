"""Create a net advance from an immutable snapshot of first-half gross pay."""
import json
from decimal import Decimal
from sqlalchemy import select
from app.models.bank_account import BankAccount
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollPeriod
from app.models.payroll_advance import PayrollAdvance
from app.models.payroll_statutory import PayrollStatutoryComponent as Component
from app.services.payroll_advance_service import (
    PayrollAdvanceError, PayrollAdvanceNotFoundError, PayrollAdvanceSourceStateError,
    get_payroll_advance,
)
from app.services.payroll_advance_basis_service import derive_payroll_advance_basis, PayrollAdvanceBasisError, money
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_statutory_service import (
    PayrollStatutoryError, resolve_statutory_rate, resolve_payroll_employee_tax_profile,
    resolve_payroll_statutory_base_rule, require_uniform_individual_rate_window,
)


@serialized_payroll_mutation(PayrollAdvanceSourceStateError)
async def create_automatic_payroll_advance(db, *, company_id, payroll_period_id,
    employment_contract_id, bank_account_id, advance_percentage, payment_date, created_by):
    percentage = Decimal(advance_percentage)
    if not percentage.is_finite() or not 0 < percentage <= 100 or percentage != percentage.quantize(Decimal('.0001')):
        raise PayrollAdvanceSourceStateError('Invalid advance percentage')
    request = dict(company_id=company_id, payroll_period_id=payroll_period_id,
        employment_contract_id=employment_contract_id, bank_account_id=bank_account_id,
        advance_percentage=format(percentage, '.4f'), payment_date=payment_date.isoformat())
    existing = await get_payroll_advance(db, company_id=company_id,
        payroll_period_id=payroll_period_id, employment_contract_id=employment_contract_id)
    if existing is not None:
        if not existing.calculation_snapshot_json:
            raise PayrollAdvanceSourceStateError('A legacy advance already exists; use its original lifecycle')
        snapshot = json.loads(existing.calculation_snapshot_json)
        if snapshot['request'] != request:
            raise PayrollAdvanceSourceStateError('Existing advance has different request data')
        return existing
    period = await db.scalar(select(PayrollPeriod).where(PayrollPeriod.company_id == company_id,
        PayrollPeriod.id == payroll_period_id))
    contract = await db.scalar(select(EmploymentContract).where(EmploymentContract.company_id == company_id,
        EmploymentContract.id == employment_contract_id))
    if period is None or contract is None:
        raise PayrollAdvanceNotFoundError('Payroll period or employment contract not found')
    if period.status != 'draft':
        raise PayrollAdvanceSourceStateError('Advance requires a draft payroll period')
    if not period.start_date.replace(day=15) <= payment_date <= period.end_date:
        raise PayrollAdvanceSourceStateError('First-half advance date must follow its earned period within the month')
    if contract.status == 'cancelled' or payment_date < contract.start_date or (contract.end_date and payment_date > contract.end_date):
        raise PayrollAdvanceSourceStateError('Advance payment date must be within active employment')
    bank = await db.scalar(select(BankAccount).where(BankAccount.company_id == company_id,
        BankAccount.id == bank_account_id))
    if bank is None or not bank.is_active or bank.currency_code != 'UAH':
        raise PayrollAdvanceSourceStateError('An active UAH bank account is required')
    try:
        basis = await derive_payroll_advance_basis(db, company_id=company_id,
            payroll_period_id=payroll_period_id, employment_contract_id=employment_contract_id,
            advance_percentage=percentage)
        if basis['currency_code'] != 'UAH':
            raise PayrollAdvanceSourceStateError('Automatic advance supports UAH salary only')
        gross = basis['recommended_gross']
        if gross <= 0:
            raise PayrollAdvanceSourceStateError('No positive advance is due')
        await require_uniform_individual_rate_window(db, company_id=company_id,
            employee_id=contract.employee_id, date_from=max(period.start_date, contract.start_date),
            date_to=payment_date)
        profile = await resolve_payroll_employee_tax_profile(db, company_id=company_id,
            employee_id=contract.employee_id, employment_contract_id=contract.id, effective_date=payment_date)
        tax_lines = []
        for component in Component:
            rate = await resolve_statutory_rate(db, company_id=company_id,
                component=component, effective_date=payment_date, employee_id=contract.employee_id)
            rule = await resolve_payroll_statutory_base_rule(db, company_id=company_id,
                component=component, employment_kind=contract.employment_kind,
                tax_profile_category=profile.category if profile else None, effective_date=payment_date)
            exempt = bool(profile and profile.category == 'exempt' and rule and rule.exemption_applies)
            if profile and profile.category == 'exempt' and rule is None:
                raise PayrollAdvanceSourceStateError('Exempt profile requires an explicit component rule')
            base = Decimal(0) if exempt else gross
            minimum_base = Decimal(0) if exempt else basis['minimum_gross']
            if component == Component.UNIFIED_SOCIAL_CONTRIBUTION and rule and rule.maximum_base_amount is not None:
                # Reserve the available monthly ceiling across this employee's
                # other advances. Reversed advances no longer consume it.
                from app.models.journal_entry import JournalEntry
                prior = (await db.execute(select(PayrollAdvance, JournalEntry.status)
                    .join(EmploymentContract, (EmploymentContract.id == PayrollAdvance.employment_contract_id) &
                          (EmploymentContract.company_id == PayrollAdvance.company_id))
                    .outerjoin(JournalEntry, (JournalEntry.payroll_advance_id == PayrollAdvance.id) &
                               (JournalEntry.company_id == company_id) & JournalEntry.reversal_of_id.is_(None))
                    .where(PayrollAdvance.company_id == company_id, PayrollAdvance.payroll_period_id == period.id,
                           EmploymentContract.employee_id == contract.employee_id))).all()
                used = Decimal(0)
                for advance, status in prior:
                    if status == 'reversed':
                        continue
                    if not advance.calculation_snapshot_json:
                        raise PayrollAdvanceSourceStateError('Monthly USC ceiling cannot be derived from a legacy advance')
                    prior_lines = json.loads(advance.calculation_snapshot_json)['tax_lines']
                    used += sum((Decimal(line['base_amount']) for line in prior_lines if line['component'] == component.value), Decimal(0))
                base = min(base, max(Decimal(0), Decimal(rule.maximum_base_amount) - used))
            tax_lines.append(dict(component=component.value, source_rate_id=rate.id,
                source_reference=rate.source_reference, rate=rate.rate, rate_effective_from=rate.effective_from,
                rate_effective_to=rate.effective_to, base_amount=base, amount=money(base * rate.rate),
                minimum_withholding=money(minimum_base * rate.rate),
                source_base_rule_id=rule.id if rule else None, exemption_applied=exempt))
    except (PayrollAdvanceBasisError, PayrollStatutoryError) as exc:
        raise PayrollAdvanceError(str(exc)) from exc
    withholding = sum((line['amount'] for line in tax_lines if line['component'] != Component.UNIFIED_SOCIAL_CONTRIBUTION.value), Decimal(0))
    minimum_withholding = sum((line['minimum_withholding'] for line in tax_lines if line['component'] != Component.UNIFIED_SOCIAL_CONTRIBUTION.value), Decimal(0))
    net = money(gross - withholding)
    minimum_net = money(basis['minimum_gross'] - minimum_withholding)
    if net <= 0 or minimum_net < 0 or net < minimum_net:
        raise PayrollAdvanceSourceStateError('Statutory rates produce an invalid net advance')
    snapshot = dict(version=1, request=request, basis=basis, gross_amount=gross,
        employee_id=contract.employee_id, tax_profile_id=profile.id if profile else None,
        tax_lines=tax_lines, withholding_amount=withholding, net_amount=net,
        minimum_net_amount=minimum_net, social_benefit_applied=False)
    row = PayrollAdvance(company_id=company_id, payroll_period_id=period.id,
        employment_contract_id=contract.id, bank_account_id=bank.id,
        advance_percentage=percentage, calculation_base_amount=basis['calculation_base_gross'],
        calculated_amount=gross, minimum_due_amount=minimum_net, paid_amount=net,
        currency_code='UAH', payment_date=payment_date, created_by=created_by,
        calculation_snapshot_json=json.dumps(snapshot, default=str, sort_keys=True, separators=(',', ':')))
    db.add(row)
    await db.flush()
    return row
