"""Real posted payroll payments, tax remittances and split 4DF writer.

The financial services, accounting journals, payment verifier, ORM
writer and PostgreSQL tables are real. Only the unapproved official
normative mapping registry is replaced by an explicitly test-only
mapping. All records are rolled back inside the isolated DB.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import json
import os

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.payroll import PayrollPeriod, PayrollPeriodStatus
from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
    PayrollRegulatoryReportRow,
)
from app.models.payroll_tax_remittance import PayrollTaxRemittanceLine
from app.services.payroll_disbursement_service import (
    create_payroll_disbursement,
    generate_and_post_payroll_disbursement_journal,
)
from app.services.payroll_tax_remittance_service import (
    register_tax_remittance,
)
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services import payroll_regulatory_form_row_writer as writer
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)

from test_payroll_insurance_funding import setup
from test_payroll_tax_remittance import payment, request


pytestmark = pytest.mark.asyncio

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


@pytest.fixture
def employee_engine():
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        pytest.fail("Isolated DATABASE_URL required")

    url = make_url(raw)

    if (
        not url.drivername.startswith("postgresql")
        or not (url.database or "").startswith("erp_r3_copy_")
    ):
        pytest.fail("Refusing source or non-isolated database")

    if url.drivername != "postgresql+asyncpg":
        url = url.set(drivername="postgresql+asyncpg")

    return create_async_engine(url, pool_pre_ping=True)


def money(value):
    result = Decimal(str(value))
    assert result.is_finite()
    assert result >= ZERO
    assert result == result.quantize(CENT)
    return result


def halves(value):
    total = money(value)
    first = (total / 2).quantize(CENT)
    second = total - first
    assert first > ZERO
    assert second > ZERO
    assert first + second == total
    return first, second


async def test_two_real_posted_payments_to_split_4df(
    employee_engine,
    monkeypatch,
):
    from app.models.employee import Employee
    from app.models.journal_entry import JournalEntryStatus

    try:
        async with AsyncSession(
            employee_engine,
            expire_on_commit=False,
        ) as db:

            (
                company,
                actor,
                contract,
                calculation,
                accrual,
                accounts,
                bank,
            ) = await setup(db)

            company_id = company.id
            actor_id = actor.id
            calculation_id = calculation.id
            employee_id = contract.employee_id
            contract_id = contract.id
            bank_id = bank.id
            period_id = calculation.payroll_period_id

            employee = await db.get(Employee, employee_id)
            assert employee is not None

            employee.payment_iban = (
                "UA213223130000026007233566001"
            )

            # Explicit synthetic tax identity for the isolated
            # PostgreSQL fixture. Never bypass readiness checks.
            employee.tax_number = "1234567890"

            # All changes occur only in the isolated test database.
            period = await db.get(PayrollPeriod, period_id)
            assert period is not None

            period.status = PayrollPeriodStatus.FINALIZED

            await db.flush()

            readiness = await payroll_regulatory_readiness(
                db,
                company_id=company_id,
                payroll_period_id=period_id,
                report_kind="4df",
            )

            assert len(readiness["calculation_sources"]) == 1
            source = readiness["calculation_sources"][0]

            assert source["payroll_calculation_id"] == calculation_id
            assert source["employee_id"] == employee_id
            assert source["employment_contract_id"] == contract_id
            assert source["gross_amount"] is not None
            assert source["net_amount"] is not None

            assert not source["issues"], source["issues"]

            gross_parts = halves(source["gross_amount"])
            net_parts = halves(source["net_amount"])

            component_totals = {
                "personal_income_tax": ZERO,
                "military_levy": ZERO,
            }

            for component in source["components"]:
                kind = component["component"]
                if kind in component_totals:
                    component_totals[kind] += money(
                        component["amount"]
                    )

            pit_parts = halves(
                component_totals["personal_income_tax"]
            )
            levy_parts = halves(
                component_totals["military_levy"]
            )

            print("REAL PAYROLL SOURCE RECONCILIATION = PASS")

            # Two genuine partial salary disbursements.
            # Unique request keys are mandatory in the production service.
            disbursements = []

            for index, amount in enumerate(net_parts, 1):
                disbursement = await create_payroll_disbursement(
                    db,
                    company_id=company_id,
                    payroll_calculation_id=calculation_id,
                    bank_account_id=bank_id,
                    cash_desk_id=None,
                    payment_date=date(2026, 9, 30),
                    created_by=actor_id,
                    amount=amount,
                    request_key=f"regulatory-real-split-pay-{index}",
                )

                journal = (
                    await generate_and_post_payroll_disbursement_journal(
                        db,
                        company_id=company_id,
                        payroll_disbursement_id=disbursement.id,
                        created_by=actor_id,
                    )
                )

                assert journal.status == JournalEntryStatus.POSTED
                assert money(disbursement.amount) == amount

                disbursements.append(disbursement)

            assert len({p.id for p in disbursements}) == 2

            print("TWO REAL POSTED PAYROLL DISBURSEMENTS = PASS")

            # Separate actual bank tax journals and remittance records.
            tax_line_ids = [[], []]

            for component, parts in (
                ("personal_income_tax", pit_parts),
                ("military_levy", levy_parts),
            ):
                for index, tax_amount in enumerate(parts):
                    bank_journal = await payment(
                        db,
                        company,
                        actor,
                        accounts,
                        bank,
                        tax_amount,
                        component,
                    )

                    remittance = await register_tax_remittance(
                        db,
                        company_id=company_id,
                        data=request(
                            bank_journal,
                            bank,
                            calculation,
                            tax_amount,
                            f"regulatory-real-{component}-{index + 1}",
                            component,
                        ),
                        created_by=actor_id,
                    )

                    await db.flush()

                    lines = list((
                        await db.scalars(
                            select(PayrollTaxRemittanceLine)
                            .where(
                                PayrollTaxRemittanceLine.company_id
                                == company_id,
                                PayrollTaxRemittanceLine.remittance_id
                                == remittance.id,
                                PayrollTaxRemittanceLine.payroll_calculation_id
                                == calculation_id,
                            )
                        )
                    ).all())

                    assert len(lines) == 1
                    assert money(lines[0].amount) == tax_amount

                    tax_line_ids[index].append(lines[0].id)

            assert len(set(tax_line_ids[0] + tax_line_ids[1])) == 4

            print("FOUR REAL POSTED TAX REMITTANCE LINES = PASS")

            # This mapping is deliberately TEST ONLY.
            # It does not modify APPROVED_MAPPINGS or grant statutory
            # approval in the production registry.
            mapping = SimpleNamespace(
                official_reference=(
                    "TEST_ONLY_4DF_NOT_APPROVED_FOR_FILING"
                ),
                source_sha256="a" * 64,
            )

            monkeypatch.setattr(
                writer,
                "approved_mappings_for",
                lambda kind, start, end: {777: mapping},
            )

            report = PayrollRegulatoryReport(
                company_id=company_id,
                payroll_period_id=period_id,
                report_kind="4df",
                revision=1,
                status="draft",
                created_by=actor_id,
            )

            db.add(report)
            await db.flush()

            report_id = report.id

            income_month = period.start_date.replace(day=1)

            allocations = []

            for index in range(2):
                allocations.append({
                    "payroll_calculation_id": calculation_id,
                    "employee_id": employee_id,
                    "slice_number": index + 1,
                    "slice_count": 2,
                    "normative_mapping_id": 777,
                    "official_reference": mapping.official_reference,
                    "normative_source_sha256":
                        mapping.source_sha256,
                    "review_reference":
                        f"REAL-POSTED-SPLIT-REVIEW-{index + 1}",
                    "category_code":
                        f"TEST_ONLY_CATEGORY_{index + 1}",
                    "income_month": income_month,
                    "payment_date": disbursements[index].payment_date,
                    "accrued_amount": gross_parts[index],
                    "pit_accrued": pit_parts[index],
                    "levy_accrued": levy_parts[index],
                    "paid_amount": net_parts[index],
                    "pit_remitted": pit_parts[index],
                    "levy_remitted": levy_parts[index],
                    "payroll_disbursement_ids": [
                        disbursements[index].id
                    ],
                    "tax_remittance_line_ids": tax_line_ids[index],
                })

            # NO PAYMENT VERIFIER MOCK: this invokes real posted-journal
            # verification, source allocation and real PostgreSQL writer.
            rows = await writer.persist_reviewed_form_rows(
                db,
                company_id=company_id,
                payroll_regulatory_report_id=report_id,
                reviewed_allocations=allocations,
            )

            assert len(rows) == 2
            assert [r.row_no for r in rows] == [1, 2]
            assert len({r.id for r in rows}) == 2

            row_ids = [r.id for r in rows]
            row_payloads = [r.payload_json for r in rows]

            for row in rows:
                payload = json.loads(row.payload_json)
                assert payload["official_generation"] == "BLOCKED"
                assert payload["source_id"] == calculation_id
                assert payload["slice_count"] == 2
                assert payload["payment_evidence_reference"].startswith(
                    "payroll_disbursements:"
                )
                assert payload[
                    "tax_remittance_evidence_reference"
                ].startswith("payroll_tax_remittance_lines:")

            print("COMBINED REAL PAYMENT + 4DF WRITER E2E = PASS")

            # Read persisted ORM rows again from actual database.
            stored = list((
                await db.scalars(
                    select(PayrollRegulatoryReportRow)
                    .where(
                        PayrollRegulatoryReportRow.company_id
                        == company_id,
                        PayrollRegulatoryReportRow.payroll_regulatory_report_id
                        == report_id,
                    )
                    .order_by(PayrollRegulatoryReportRow.row_no)
                    .execution_options(populate_existing=True)
                )
            ).all())

            assert [r.id for r in stored] == row_ids
            assert [r.payload_json for r in stored] == row_payloads

            print("REAL POSTGRESQL REPORT ROW RE-READ = PASS")

            repeat = await writer.persist_reviewed_form_rows(
                db,
                company_id=company_id,
                payroll_regulatory_report_id=report_id,
                reviewed_allocations=allocations,
            )

            assert [r.id for r in repeat] == row_ids

            print("REAL FINANCIAL SPLIT IDEMPOTENCY = PASS")

            changed = deepcopy(allocations)
            changed[0]["review_reference"] = "UNAUTHORIZED-REWRITE"

            with pytest.raises(PayrollRegulatoryReportError):
                await writer.persist_reviewed_form_rows(
                    db,
                    company_id=company_id,
                    payroll_regulatory_report_id=report_id,
                    reviewed_allocations=changed,
                )

            after = list((
                await db.scalars(
                    select(PayrollRegulatoryReportRow)
                    .where(
                        PayrollRegulatoryReportRow.company_id
                        == company_id,
                        PayrollRegulatoryReportRow.payroll_regulatory_report_id
                        == report_id,
                    )
                    .order_by(PayrollRegulatoryReportRow.row_no)
                    .execution_options(populate_existing=True)
                )
            ).all())

            assert [r.payload_json for r in after] == row_payloads

            print("REAL SPLIT SNAPSHOT REWRITE REJECTED = PASS")

            duplicate = deepcopy(allocations)
            duplicate[1]["payroll_disbursement_ids"] = (
                duplicate[0]["payroll_disbursement_ids"]
            )

            with pytest.raises(
                PayrollRegulatoryReportError,
                match="allocated more than once",
            ):
                await writer.persist_reviewed_form_rows(
                    db,
                    company_id=company_id,
                    payroll_regulatory_report_id=report_id,
                    reviewed_allocations=duplicate,
                )

            print("DUPLICATE REAL PAYMENT ALLOCATION BLOCKED = PASS")

            assert report.status == "draft"
            assert report.generated_at is None

            print("OFFICIAL STATUTORY GENERATION = BLOCKED")

            await db.rollback()

    finally:
        await employee_engine.dispose()
