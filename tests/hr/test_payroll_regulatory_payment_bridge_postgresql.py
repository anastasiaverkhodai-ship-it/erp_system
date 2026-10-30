"""Real PostgreSQL E2E for verified 4DF payment evidence.

All writes occur on the regression-copy database only.
Normative mapping and statutory report generation are not faked.
"""
import copy
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_insurance_funding import setup
from test_payroll_tax_remittance import payment, request

from app.models.employee import Employee
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_tax_remittance import PayrollTaxRemittanceLine
from app.services.payroll_disbursement_service import (
    create_payroll_disbursement,
    generate_and_post_payroll_disbursement_journal,
)
from app.services.payroll_tax_remittance_service import (
    register_tax_remittance,
    reverse_tax_remittance,
)
from app.services.payroll_regulatory_payment_evidence import (
    verify_4df_posted_payment_evidence,
    active_posted_journal_query,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)

pytestmark = pytest.mark.asyncio


async def create_real_financial_case(db):
    (
        company,
        user,
        contract,
        calculation,
        accrual,
        accounts,
        bank,
    ) = await setup(db)

    employee = await db.get(
        Employee,
        contract.employee_id,
    )

    assert employee is not None
    assert employee.company_id == company.id

    employee.payment_iban = (
        "UA213223130000026007233566001"
    )

    await db.flush()

    payroll_payment = await create_payroll_disbursement(
        db,
        company_id=company.id,
        payroll_calculation_id=calculation.id,
        bank_account_id=bank.id,
        payment_date=date(2026, 9, 30),
        created_by=user.id,
    )

    await db.flush()

    posted_payroll_journal = (
        await generate_and_post_payroll_disbursement_journal(
            db,
            company_id=company.id,
            payroll_disbursement_id=payroll_payment.id,
            created_by=user.id,
        )
    )

    from app.models.journal_entry import JournalEntryStatus

    assert posted_payroll_journal.id is not None
    assert (
        posted_payroll_journal.status
        == JournalEntryStatus.POSTED
    )

    pit_amount = Decimal("702.00")
    levy_amount = Decimal("195.00")

    pit_journal = await payment(
        db,
        company,
        user,
        accounts,
        bank,
        pit_amount,
        "personal_income_tax",
    )

    pit_remittance = await register_tax_remittance(
        db,
        company_id=company.id,
        data=request(
            pit_journal,
            bank,
            calculation,
            pit_amount,
            "regulatory-e2e-pit",
            "personal_income_tax",
        ),
        created_by=user.id,
    )

    levy_journal = await payment(
        db,
        company,
        user,
        accounts,
        bank,
        levy_amount,
        "military_levy",
    )

    levy_remittance = await register_tax_remittance(
        db,
        company_id=company.id,
        data=request(
            levy_journal,
            bank,
            calculation,
            levy_amount,
            "regulatory-e2e-levy",
            "military_levy",
        ),
        created_by=user.id,
    )

    await db.flush()

    remittance_lines = list((
        await db.scalars(
            select(PayrollTaxRemittanceLine).where(
                PayrollTaxRemittanceLine.company_id
                    == company.id,
                PayrollTaxRemittanceLine.remittance_id.in_(
                    [pit_remittance.id, levy_remittance.id]
                ),
                PayrollTaxRemittanceLine.payroll_calculation_id
                    == calculation.id,
            )
        )
    ).all())

    assert len(remittance_lines) == 2

    allocation = {
        "payroll_calculation_id": calculation.id,
        "_verified_employee_id": contract.employee_id,
        "payroll_disbursement_ids": [payroll_payment.id],
        "tax_remittance_line_ids": [
            row.id for row in remittance_lines
        ],
        "paid_amount": Decimal(payroll_payment.amount),
        "payment_date": payroll_payment.payment_date,
        "pit_remitted": pit_amount,
        "levy_remitted": levy_amount,
    }

    return (
        company,
        user,
        calculation,
        payroll_payment,
        pit_remittance,
        allocation,
    )


async def test_real_posted_payments_pass_bridge(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company,
            user,
            calculation,
            payroll_payment,
            pit_remittance,
            allocation,
        ) = await create_real_financial_case(db)

        await verify_4df_posted_payment_evidence(
            db,
            company_id=company.id,
            reviewed_allocations=[allocation],
        )

        assert allocation["payment_evidence_reference"].startswith(
            "payroll_disbursements:"
        )
        assert allocation[
            "tax_remittance_evidence_reference"
        ].startswith("payroll_tax_remittance_lines:")

        assert await db.scalar(
            active_posted_journal_query(
                company_id=company.id,
                payroll_disbursement_id=payroll_payment.id,
            )
        ) is not None

        await db.rollback()


async def test_real_cross_company_evidence_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company,
            user,
            calculation,
            payroll_payment,
            pit_remittance,
            allocation,
        ) = await create_real_financial_case(db)

        with pytest.raises(PayrollRegulatoryReportError):
            await verify_4df_posted_payment_evidence(
                db,
                company_id=company.id + 1000000,
                reviewed_allocations=[allocation],
            )

        await db.rollback()


async def test_real_wrong_employee_rejected(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company,
            user,
            calculation,
            payroll_payment,
            pit_remittance,
            allocation,
        ) = await create_real_financial_case(db)

        allocation["_verified_employee_id"] += 1000000

        with pytest.raises(
            PayrollRegulatoryReportError,
            match="employee mismatch",
        ):
            await verify_4df_posted_payment_evidence(
                db,
                company_id=company.id,
                reviewed_allocations=[allocation],
            )

        await db.rollback()


async def test_real_payment_amount_mismatch_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company,
            user,
            calculation,
            payroll_payment,
            pit_remittance,
            allocation,
        ) = await create_real_financial_case(db)

        allocation["paid_amount"] += Decimal("0.01")

        with pytest.raises(
            PayrollRegulatoryReportError,
            match="payment amount differs",
        ):
            await verify_4df_posted_payment_evidence(
                db,
                company_id=company.id,
                reviewed_allocations=[allocation],
            )

        await db.rollback()


async def test_real_duplicate_document_allocation_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company,
            user,
            calculation,
            payroll_payment,
            pit_remittance,
            allocation,
        ) = await create_real_financial_case(db)

        duplicate = copy.deepcopy(allocation)

        with pytest.raises(
            PayrollRegulatoryReportError,
            match="more than once",
        ):
            await verify_4df_posted_payment_evidence(
                db,
                company_id=company.id,
                reviewed_allocations=[allocation, duplicate],
            )

        await db.rollback()


async def test_real_reversed_tax_payment_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company,
            user,
            calculation,
            payroll_payment,
            pit_remittance,
            allocation,
        ) = await create_real_financial_case(db)

        await reverse_tax_remittance(
            db,
            company_id=company.id,
            remittance_id=pit_remittance.id,
            reversal_date=date(2026, 9, 30),
            reversed_by=user.id,
        )

        await db.flush()

        with pytest.raises(
            PayrollRegulatoryReportError,
            match="not actively posted",
        ):
            await verify_4df_posted_payment_evidence(
                db,
                company_id=company.id,
                reviewed_allocations=[allocation],
            )

        await db.rollback()
