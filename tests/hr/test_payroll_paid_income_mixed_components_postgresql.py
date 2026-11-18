"""Real mixed salary/sick settlement attribution on the disposable copy."""
import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_insurance_funding import setup, receipt, request
from app.models.accounting_period import AccountingPeriod
from app.models.employee import Employee
from app.models.payroll_paid_income import PayrollPaidIncome
from app.services.payroll_disbursement_service import (
    create_payroll_disbursement, generate_and_post_payroll_disbursement_journal,
    reverse_payroll_disbursement_journal,
)
from app.services.payroll_insurance_funding_service import register_insurance_funding
from app.services.payroll_paid_income_service import read_paid_income

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_E2E") != "1", reason="Disposable PostgreSQL required")]


@pytest.mark.parametrize("payment_day,report_month", [
    (date(2026, 9, 30), "2026-09"),
    (date(2026, 10, 7), None),  # No approved mixed-income month mapping.
    (date(2026, 11, 2), "2026-11"),
])
async def test_mixed_components_partial_full_reversal_and_repayment(employee_engine, payment_day, report_month):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        assert (await db.scalar(text("SELECT current_database()"))).startswith("erp_r3_copy_")
        company, actor, contract, calculation, _, accounts, bank = await setup(db)
        # Extend only this isolated fixture; production posting guards still apply.
        for month, last_day in ((10, 31), (11, 30)):
            db.add(AccountingPeriod(
                company_id=company.id, year=2026, month=month,
                start_date=date(2026, month, 1), end_date=date(2026, month, last_day),
                status="open", is_locked=False,
            ))
        await db.flush()
        journal = await receipt(db, company, actor, accounts, bank, Decimal("400"))
        await register_insurance_funding(db, company_id=company.id,
            data=request(journal, bank, calculation, Decimal("400"), "mixed-components-funding"),
            created_by=actor.id)
        employee = await db.get(Employee, contract.employee_id)
        employee.payment_iban = "UA213223130000026007233566001"
        await db.flush()
        args = dict(company_id=company.id, payroll_calculation_id=calculation.id)
        payments = []
        frozen = {}
        for number, amount in ((1, Decimal("100")), (2, None)):
            payment = await create_payroll_disbursement(db, **args,
                bank_account_id=bank.id, payment_date=payment_day,
                created_by=actor.id, amount=amount, request_key=f"mixed-components-{number}")
            await generate_and_post_payroll_disbursement_journal(db, company_id=company.id,
                payroll_disbursement_id=payment.id, created_by=actor.id)
            payments.append(payment)
            review = await read_paid_income(db, **args)
            assert review["status"] == ("COMPLETE" if report_month else "INCOMPLETE_SOURCE_DATA")
            assert all(row["reporting_month"] == report_month for row in review["rows"])
            if report_month is None:
                assert review["issues"]
                assert all("classification" in issue["reason"] for issue in review["issues"])
            for row in review["rows"]:
                assert row["component_evidence"] is not None, row["component_issue"]
                assert row["component_issue"] is None
                parts = row["component_evidence"]["body"]["slices"]
                assert sum((Decimal(part["gross"]) for part in parts), Decimal("0")) == Decimal(row["gross_paid"])
                assert all(part["calculation_id"] == calculation.id for part in parts)
                assert all(part["accrual_month"] == "2026-09" for part in parts)
                if row["snapshot_id"] in frozen:
                    assert frozen[row["snapshot_id"]] == row["component_evidence"]
                frozen[row["snapshot_id"]] = row["component_evidence"]
        totals = {}
        for row in review["rows"]:
            for part in row["component_evidence"]["body"]["slices"]:
                key = (part["income_type"], part["funding"])
                totals[key] = totals.get(key, Decimal("0")) + Decimal(part["gross"])
        assert totals == {("salary", "employer"): Decimal("3100"),
            ("sick_pay", "employer"): Decimal("400"),
            ("sick_pay", "insurer"): Decimal("400")}
        assert review["official_form_approved"] is False
        await reverse_payroll_disbursement_journal(db, company_id=company.id,
            payroll_disbursement_id=payments[1].id, reversal_date=payment_day, reversed_by=actor.id)
        remaining = await read_paid_income(db, **args)
        assert len(remaining["rows"]) == 1
        assert remaining["rows"][0]["component_evidence"] == frozen[remaining["rows"][0]["snapshot_id"]]
        replacement = await create_payroll_disbursement(db, **args,
            bank_account_id=bank.id, payment_date=payment_day, created_by=actor.id,
            request_key="mixed-components-repayment")
        await generate_and_post_payroll_disbursement_journal(db, company_id=company.id,
            payroll_disbursement_id=replacement.id, created_by=actor.id)
        final = await read_paid_income(db, **args)
        assert final["status"] == ("COMPLETE" if report_month else "INCOMPLETE_SOURCE_DATA")
        assert final["gross_paid_by_month"] == ({report_month: "3900.00"} if report_month else {})
        assert sum((Decimal(row["gross_paid"]) for row in final["rows"]), Decimal("0")) == Decimal("3900.00")
        assert len(final["rows"]) == 2
        assert all(row["component_evidence"] is not None for row in final["rows"])
        records = list((await db.scalars(select(PayrollPaidIncome).where(
            PayrollPaidIncome.company_id == company.id))).all())
        assert len(records) == 3  # The reversed snapshot remains immutable history.
        frozen_records = {row.id: row.snapshot_json for row in records}
        await db.commit()
        async with AsyncSession(employee_engine) as reader:
            reread = list((await reader.scalars(select(PayrollPaidIncome).where(
                PayrollPaidIncome.company_id == company.id))).all())
            assert {row.id: row.snapshot_json for row in reread} == frozen_records
            committed = await read_paid_income(reader, **args)
            assert committed == final
            from app.services.payroll_paid_income_service import allocation_from_record
            from app.services.payroll_paid_income_components import verify_component_evidence
            for record in reread:
                body = allocation_from_record(record)
                verify_component_evidence(body["component_evidence"])
