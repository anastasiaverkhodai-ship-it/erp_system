"""Native payroll revisions preserve frozen inputs under PostgreSQL seals."""
from calendar import monthrange
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_secondary_sick_postgresql import prepare
from test_payroll_sick_correction_preview_postgresql import changed_evidence, install_revision_guards
from test_payroll_correction_basis_postgresql import protect
from app.models.leave_request import LeaveRequest
from app.models.payroll import PayrollPeriod, PayrollInput, PayrollCalculation, PayrollCalculationLine
from app.services.payroll_sick_history_service import calculate_sick_from_history
from app.services.payroll_sick_replacement_source_service import materialize_sick_replacement
from app.services.payroll_sick_payroll_revision_service import build_sick_payroll_revision
from app.services.payroll_correction_basis import make_basis, replay_basis, verify_basis
from app.services.payroll_revision_service import current_calculation_filter
from app.services.payroll_sick_allocation import sick_period_amount

pytestmark = pytest.mark.asyncio


async def payroll_for_sick(db, company, source, actor, *, extra_sources=(), period_start=None, severance_factory=None):
    leave = await db.get(LeaveRequest, source.leave_request_id)
    start = period_start or leave.start_date.replace(day=1)
    end = date(start.year, start.month, monthrange(start.year, start.month)[1])
    period = PayrollPeriod(company_id=company.id, year=start.year, month=start.month,
        start_date=start, end_date=end, created_by=actor)
    db.add(period)
    await db.flush()
    payroll_input = PayrollInput(company_id=company.id, payroll_period_id=period.id,
        employment_contract_id=source.employment_contract_id, created_by=actor)
    db.add(payroll_input)
    await db.flush()
    days, amount = sick_period_amount(source, leave, start, end)
    lines = [dict(line_no=1, line_type='sick_pay', description='Sick leave', quantity=D(days),
        rate=source.daily_benefit_amount, amount=amount, currency_code='UAH',
        source_sick_leave_calculation_id=source.id)]
    for extra in extra_sources:
        extra_leave = await db.get(LeaveRequest, extra.leave_request_id)
        extra_days, extra_amount = sick_period_amount(extra, extra_leave, start, end)
        lines.append(dict(line_no=len(lines)+1, line_type='sick_pay', description='Sick leave',
            quantity=D(extra_days), rate=extra.daily_benefit_amount, amount=extra_amount,
            currency_code='UAH', source_sick_leave_calculation_id=extra.id))
        amount += extra_amount
    facts = dict(rate_type='monthly', minimum_rate='8647', ordinary_worked_minutes=0,
        default_norm_minutes=10560, earnings=[dict(category='sick_pay', amount=str(amount))],
        adjustment='0', has_adjustment=False, period_start=start.isoformat(),
        period_end=end.isoformat(), rule_version='UA-4695-IX-2026-v1')
    basis = make_basis(company_id=company.id, period=period, payroll_input=payroll_input,
        salary_slices=[], facts=facts, lines=lines)
    gross, lines, assessment = replay_basis(basis, {})
    if severance_factory is not None:
        extra = await severance_factory(payroll_input,period)
        extra['line_no']=len(lines)+1
        lines.append(extra)
        gross += extra['amount']
    calc = PayrollCalculation(company_id=company.id, payroll_period_id=period.id,
        payroll_input_id=payroll_input.id, employment_contract_id=source.employment_contract_id,
        currency_code='UAH', gross_amount=gross, calculated_by=actor,
        minimum_wage_assessment=dict(assessment, correction_basis=basis))
    db.add(calc)
    await db.flush()
    db.add_all([PayrollCalculationLine(company_id=company.id, payroll_calculation_id=calc.id, **line)
                for line in lines])
    await db.flush()
    await db.refresh(calc)
    return calc


async def test_revision_replays_real_sick_source_and_seals_new_payroll(employee_engine):
    await install_revision_guards(employee_engine)
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, _, _, _, args = await prepare(db)
        original_sick = await calculate_sick_from_history(db, **args)
        original = await payroll_for_sick(db, company, original_sick, args['calculated_by'])
        old_gross = original.gross_amount
        evidence = await changed_evidence(db, company, args)
        replacement_sick = await materialize_sick_replacement(db, company_id=company.id,
            original_calculation_id=original_sick.id, insurance_evidence_id=evidence.id,
            calculated_by=args['calculated_by'])
        replacement = await build_sick_payroll_revision(db, company_id=company.id, original=original,
            original_sick=original_sick, replacement_sick=replacement_sick,
            request_key='sick-payroll-revision', reason='Verified corrected insurance history',
            actor_id=args['calculated_by'])
        assert replacement.gross_amount != old_gross
        assert original.gross_amount == old_gross
        assert replacement.revision == 2
        assert replacement.payroll_input_id == original.payroll_input_id
        lines = list((await db.scalars(select(PayrollCalculationLine).where(
            PayrollCalculationLine.payroll_calculation_id == replacement.id)
            .order_by(PayrollCalculationLine.line_no))).all())
        verify_basis(replacement, lines)
        assert lines[0].source_sick_leave_calculation_id == replacement_sick.id
        assert list((await db.scalars(select(PayrollCalculation.id).where(
            PayrollCalculation.company_id == company.id, current_calculation_filter()))).all()) == [replacement.id]


async def test_settlement_chain_requires_matching_native_correction(employee_engine):
    from app.models.payroll_correction import PayrollCorrection
    from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication, PayrollSickPayrollRevision
    from app.services.payroll_sick_financial_sources import sick_settlement_chain, SickFinancialSourceError
    from sqlalchemy import delete
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, _, _, _, args = await prepare(db)
        old_sick = await calculate_sick_from_history(db, **args)
        original = await payroll_for_sick(db, company, old_sick, args['calculated_by'])
        assert await sick_settlement_chain(db, company_id=company.id, calculation_id=original.id) == (original.id,)
        # This fixture uses metadata without the database seal trigger.
        from app.services.payroll_correction_basis import SEAL
        original.minimum_wage_assessment = dict(original.minimum_wage_assessment,
            correction_basis=dict(original.minimum_wage_assessment['correction_basis'], seal=SEAL))
        await db.flush()
        evidence = await changed_evidence(db, company, args)
        new_sick = await materialize_sick_replacement(db, company_id=company.id,
            original_calculation_id=old_sick.id, insurance_evidence_id=evidence.id,
            calculated_by=args['calculated_by'])
        replacement = await build_sick_payroll_revision(db, company_id=company.id, original=original,
            original_sick=old_sick, replacement_sick=new_sick, request_key='native-link',
            reason='Verified corrected insurance history', actor_id=args['calculated_by'])
        application = PayrollSickCorrectionApplication(company_id=company.id,
            original_sick_id=old_sick.id, replacement_sick_id=new_sick.id,
            request_key='application', request_fingerprint='a'*64, reason='Verified insurance history',
            correction_date=date(2026,10,26), created_by=args['calculated_by'])
        db.add(application)
        await db.flush()
        db.add(PayrollSickPayrollRevision(company_id=company.id, application_id=application.id,
            original_calculation_id=original.id, replacement_calculation_id=replacement.id,
            original_financial={}, replacement_financial={}, net_delta=D(0)))
        await db.flush()
        assert await sick_settlement_chain(db, company_id=company.id, calculation_id=replacement.id) == (replacement.id, original.id)
        with pytest.raises(SickFinancialSourceError, match='not found'):
            await sick_settlement_chain(db, company_id=company.id+999, calculation_id=replacement.id)
        await db.execute(delete(PayrollCorrection).where(PayrollCorrection.company_id == company.id))
        with pytest.raises(SickFinancialSourceError, match='Invalid financial'):
            await sick_settlement_chain(db, company_id=company.id, calculation_id=replacement.id)
