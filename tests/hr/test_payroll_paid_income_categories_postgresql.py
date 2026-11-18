"""Source-bound financial attribution; no statutory category approval."""
import os
from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_leave_final_settlement_postgresql import (
    test_termination_compensation_flows_once_through_payroll_and_payment as termination_flow,
)
from test_payroll_leave_correction_financial_postgresql import setup as vacation_setup
from app.models.payroll import PayrollCalculation
from app.models.payroll_paid_income import PayrollPaidIncome
from app.services.payroll_paid_income_service import read_paid_income, allocation_from_record
from app.services.payroll_paid_income_components import verify_component_evidence
from app.services.payroll_disbursement_service import (
    create_payroll_disbursement, generate_and_post_payroll_disbursement_journal,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv('RUN_POSTGRES_E2E') != '1', reason='Disposable PostgreSQL required')]


async def assert_components(engine, company, calc, expected, expected_month):
    async with AsyncSession(engine) as reader:
        review = await read_paid_income(reader, company_id=company, payroll_calculation_id=calc)
        assert review['official_form_approved'] is False
        assert review['status'] == ('COMPLETE' if expected_month else 'INCOMPLETE_SOURCE_DATA')
        assert all(row['reporting_month'] == expected_month for row in review['rows'])
        totals = {}
        for row in review['rows']:
            assert row['component_issue'] is None
            body = verify_component_evidence(row['component_evidence'])
            for part in body['slices']:
                key = part['income_type']
                totals[key] = totals.get(key, Decimal(0)) + Decimal(part['gross'])
                assert part['company_id'] == company and part['calculation_id'] == calc
                if key == 'leave_compensation':
                    assert part['provenance']['source_leave_compensation_line_id']
                elif key == 'severance':
                    assert part['provenance']['source_termination_severance_id']
                elif key == 'vacation_pay':
                    assert part['provenance']['source_vacation_calculation_id']
        assert totals == expected
        records = list((await reader.scalars(select(PayrollPaidIncome).where(
            PayrollPaidIncome.company_id == company))).all())
        assert len(records) == len(review['rows'])
        frozen = {r.id: r.snapshot_json for r in records}
        for record in records:
            verify_component_evidence(allocation_from_record(record)['component_evidence'])
        from app.services.payroll_regulatory_withholding_source_reconciliation import WithholdingReconciliationError
        with pytest.raises(WithholdingReconciliationError, match='not found for company'):
            await read_paid_income(reader, company_id=company + 999999,
                                   payroll_calculation_id=calc)
    async with AsyncSession(engine) as reader:
        reread = list((await reader.scalars(select(PayrollPaidIncome).where(
            PayrollPaidIncome.company_id == company))).all())
        assert {r.id: r.snapshot_json for r in reread} == frozen


@pytest.mark.parametrize('with_severance', [False, True])
async def test_committed_termination_component_provenance(employee_engine, with_severance):
    async with AsyncSession(employee_engine) as db:
        assert (await db.scalar(text('SELECT current_database()'))).startswith('erp_r3_copy_')
    await termination_flow(employee_engine, with_severance)
    async with AsyncSession(employee_engine) as db:
        calc = (await db.scalars(select(PayrollCalculation))).one()
        company, calc_id = calc.company_id, calc.id
    expected = {'salary': Decimal('22000'), 'leave_compensation': Decimal('1700')}
    if with_severance:
        expected['severance'] = Decimal('12000')
    await assert_components(employee_engine, company, calc_id, expected, '2026-09')


@pytest.mark.parametrize('payment_day,expected_month', [(date(2026,9,30), '2026-09'),
                                                       (date(2026,10,7), None)])
async def test_committed_vacation_payment_attribution(employee_engine, payment_day, expected_month):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        assert (await db.scalar(text('SELECT current_database()'))).startswith('erp_r3_copy_')
        company, actor, _, _, calc, bank, _ = await vacation_setup(db)
        payment = await create_payroll_disbursement(db, company_id=company.id,
            payroll_calculation_id=calc.id, bank_account_id=bank.id,
            payment_date=payment_day, created_by=actor.id, request_key='vacation-attribution')
        posted = await generate_and_post_payroll_disbursement_journal(db, company_id=company.id,
            payroll_disbursement_id=payment.id, created_by=actor.id)
        again = await generate_and_post_payroll_disbursement_journal(db, company_id=company.id,
            payroll_disbursement_id=payment.id, created_by=actor.id)
        assert posted.id == again.id
        company_id, calc_id = company.id, calc.id
        await db.commit()
    await assert_components(employee_engine, company_id, calc_id,
                            {'vacation_pay': Decimal('1000')}, expected_month)
