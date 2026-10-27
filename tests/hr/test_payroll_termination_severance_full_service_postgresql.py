"""Full termination severance -> normal Payroll Calculation service E2E."""

import os
import re

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.models.employment_event import EmploymentEvent
from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
    list_payroll_calculation_lines,
    PayrollCalculationConflictError,
)
from app.services.payroll_termination_severance_preflight import (
    SeverancePreflightError,
    preflight_termination_severance,
)
from app.services.payroll_statutory_service import (
    PayrollStatutoryValidationError,
    calculate_payroll_statutory_result,
)
from test_payroll_vacation_postgresql import _seed_identity


pytestmark = pytest.mark.asyncio


def require_clone():
    assert os.getenv("RUN_POSTGRES_E2E") == "1"

    url = make_url(str(settings.database_url))
    assert re.fullmatch(
        r"erp_r3_copy_[0-9a-f]{16}",
        url.database or "",
    )
    assert url.host in ("localhost", "127.0.0.1", "::1")

    return str(settings.database_url)


async def test_full_payroll_service_with_severance():
    engine = create_async_engine(
        require_clone(), pool_pre_ping=True
    )

    Session = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    token = uuid4().hex[:10]

    try:
        async with engine.connect() as conn:
            head = await conn.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            assert head == "13d4a7b8c063"

            # A revision stamp alone does not prove the snapshot schema
            # and its database-level immutability guards are installed.
            snapshot_table = "payroll_severance_statutory_snapshots"
            from app.models.payroll_severance_statutory_record import (
                PayrollSeveranceStatutoryRecord,
            )

            columns = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_columns(
                    snapshot_table, schema="public"
                )
            )
            assert {
                column.name
                for column in PayrollSeveranceStatutoryRecord.__table__.columns
            } <= {column["name"] for column in columns}

            triggers = (await conn.execute(text("""
                SELECT tgname, pg_get_triggerdef(oid) AS definition
                FROM pg_trigger
                WHERE tgrelid =
                    'public.payroll_severance_statutory_snapshots'::regclass
                  AND NOT tgisinternal
                  AND tgenabled IN ('O', 'A')
            """))).mappings().all()
            definitions = {row["tgname"]: row["definition"] for row in triggers}
            assert "BEFORE INSERT OR DELETE OR UPDATE" in definitions.get(
                "trg_psss_validate", ""
            )
            assert "payroll_severance_snapshot_validate()" in definitions[
                "trg_psss_validate"
            ]
            assert "BEFORE TRUNCATE" in definitions.get(
                "trg_psss_no_truncate", ""
            )
            assert "payroll_severance_snapshot_no_truncate()" in definitions[
                "trg_psss_no_truncate"
            ]

        async with Session() as db:
            async with db.begin():

                company, user, employee, contract = (
                    await _seed_identity(db)
                )

                contract.status = "ended"
                contract.end_date = date(2026, 9, 30)
                await db.flush()

                period = PayrollPeriod(
                    company_id=company.id,
                    year=2026,
                    month=9,
                    start_date=date(2026, 9, 1),
                    end_date=date(2026, 9, 30),
                    status=PayrollPeriodStatus.FINALIZED,
                    created_by=user.id,
                    finalized_by=user.id,
                    finalized_at=datetime(
                        2026, 10, 1, 12, 0, tzinfo=UTC
                    ),
                )
                db.add(period)
                await db.flush()

                payroll_input = PayrollInput(
                    company_id=company.id,
                    payroll_period_id=period.id,
                    employment_contract_id=contract.id,
                    scheduled_minutes=1200,
                    worked_minutes=1200,
                    leave_days=0,
                    sick_days=0,
                    manual_adjustment_amount=Decimal("0.00"),
                    created_by=user.id,
                )
                db.add(payroll_input)
                await db.flush()

                rate = EmployeeSalaryRate(
                    company_id=company.id,
                    employment_contract_id=contract.id,
                    rate_type="hourly",
                    amount=Decimal("20.00"),
                    currency_code="UAH",
                    effective_from=date(2026, 9, 1),
                    effective_to=date(2026, 9, 30),
                    created_by=user.id,
                )
                db.add(rate)
                await db.flush()

                salary_slice = PayrollInputSalarySlice(
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    salary_rate_id=rate.id,
                    salary_rate_type="hourly",
                    salary_rate_amount=Decimal("20.00"),
                    currency_code="UAH",
                    effective_from=date(2026, 9, 1),
                    effective_to=date(2026, 9, 30),
                    scheduled_minutes=1200,
                    worked_minutes=1200,
                )
                db.add(salary_slice)

                event = EmploymentEvent(
                    company_id=company.id,
                    employment_contract_id=contract.id,
                    event_type="termination",
                    effective_date=date(2026, 9, 30),
                    order_number=f"TERM-{token}",
                    order_date=date(2026, 9, 30),
                    reason="Documented redundancy",
                    request_key=f"term-{token}",
                    before_state={},
                    after_state={},
                    request_payload={
                        "termination_basis": {
                            "ground": "redundancy",
                            "labour_code_provision": "40.1",
                            "source_reference": "Verified termination order",
                        }
                    },
                    created_by=user.id,
                )
                db.add(event)
                await db.flush()

                source = PayrollTerminationSeverance(
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    employment_contract_id=contract.id,
                    termination_event_id=event.id,
                    revision=1,
                    supersedes_id=None,
                    request_key=f"severance-{token}",
                    termination_date=date(2026, 9, 30),
                    order_number=event.order_number,
                    policy_ground="redundancy",
                    policy_category="average_months",
                    labour_code_provision="40.1",
                    source_reference="Verified termination order",
                    multiplier=1,
                    basis_as_of=date(2026, 9, 30),
                    basis_reference="Verified average monthly earnings",
                    base_amount=Decimal("12000.00"),
                    gross_amount=Decimal("12000.00"),
                    currency_code="UAH",
                    created_by=user.id,
                )
                db.add(source)
                await db.flush()

                # The existing production Payroll service must
                # construct BOTH salary and severance lines.
                calculation = await calculate_payroll_input(
                    db,
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    calculated_by=user.id,
                )
                await db.flush()

                lines = await list_payroll_calculation_lines(
                    db,
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                )

                salary_lines = [
                    x for x in lines if x.line_type == "salary"
                ]
                severance_lines = [
                    x for x in lines if x.line_type == "severance"
                ]

                assert len(salary_lines) == 1
                assert len(severance_lines) == 1
                assert Decimal(salary_lines[0].amount) == Decimal("400.00")
                assert Decimal(severance_lines[0].amount) == Decimal("12000.00")

                assert (
                    severance_lines[0].source_termination_severance_id
                    == source.id
                )

                # Inspect and reconcile every payroll component.
                # Do not silently accept an unexplained gross difference.
                amounts = {
                    line.line_type: Decimal(line.amount)
                    for line in lines
                }

                print("PAYROLL COMPONENTS =", amounts, flush=True)
                print(
                    "PAYROLL GROSS =",
                    Decimal(calculation.gross_amount),
                    flush=True,
                )

                assert len(amounts) == len(lines), (
                    "Duplicate payroll line types require review"
                )

                assert amounts == {
                    "salary": Decimal("400.00"),
                    "minimum_wage": Decimal("640.00"),
                    "severance": Decimal("12000.00"),
                }, amounts

                assert (
                    sum(amounts.values(), Decimal("0.00"))
                    == Decimal(calculation.gross_amount)
                    == Decimal("13040.00")
                )
                assert calculation.currency_code == "UAH"

                repeated = await calculate_payroll_input(
                    db,
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    calculated_by=user.id,
                )

                assert repeated.id == calculation.id

                line_count = await db.scalar(
                    select(func.count(PayrollCalculationLine.id))
                    .where(
                        PayrollCalculationLine.company_id == company.id,
                        PayrollCalculationLine.payroll_calculation_id
                        == calculation.id,
                    )
                )
                assert line_count == 3

                with pytest.raises(
                    PayrollStatutoryValidationError,
                    match="Severance statutory classification",
                ):
                    await calculate_payroll_statutory_result(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=calculation.id,
                        actor_user_id=user.id,
                    )



                # C4-C1 FINANCIAL NEGATIVE E2E
                # All financial entry points must fail closed
                # before returning old data or writing new data.
                from unittest.mock import patch

                from app.services.payroll_deduction_result_service import (
                    calculate_payroll_deduction_result,
                )
                from app.services.payroll_accounting_service import (
                    generate_and_post_payroll_journal_entry,
                )
                from app.services.payroll_payslip_service import (
                    generate_payroll_payslip,
                )

                financial_tables = (
                    "payroll_statutory_results",
                    "payroll_deduction_results",
                    "payroll_payslips",
                    "journal_entries",
                )

                async def financial_counts():
                    return {
                        table: await db.scalar(
                            text(
                                f"SELECT count(*) FROM {table} "
                                "WHERE company_id = :company_id"
                            ),
                            {"company_id": company.id},
                        )
                        for table in financial_tables
                    }

                counts_before = await financial_counts()

                # Patch ONLY previous-result lookups. If a guard
                # is mistakenly moved behind one, the test fails.
                financial_paths = (
                    (
                        "STATUTORY",
                        "app.services.payroll_statutory_service."
                        "get_statutory_result_for_calculation",
                        lambda: calculate_payroll_statutory_result(
                            db,
                            company_id=company.id,
                            payroll_calculation_id=calculation.id,
                            actor_user_id=user.id,
                        ),
                    ),
                    (
                        "DEDUCTIONS",
                        "app.services.payroll_deduction_result_service."
                        "get_payroll_deduction_result",
                        lambda: calculate_payroll_deduction_result(
                            db,
                            company_id=company.id,
                            payroll_calculation_id=calculation.id,
                            calculated_by=user.id,
                        ),
                    ),
                    (
                        "PAYSLIP",
                        "app.services.payroll_payslip_service."
                        "get_payroll_payslip_by_calculation",
                        lambda: generate_payroll_payslip(
                            db,
                            company_id=company.id,
                            payroll_calculation_id=calculation.id,
                            actor_user_id=user.id,
                        ),
                    ),
                    (
                        "ACCOUNTING",
                        "app.services.payroll_accounting_service."
                        "get_payroll_accounting_journal",
                        lambda: generate_and_post_payroll_journal_entry(
                            db,
                            company_id=company.id,
                            payroll_calculation_id=calculation.id,
                            created_by=user.id,
                        ),
                    ),
                )

                for name, lookup_target, invoke in financial_paths:
                    with patch(
                        lookup_target,
                        side_effect=AssertionError(
                            f"{name}: old-result lookup reached"
                        ),
                    ) as forbidden_lookup:
                        with pytest.raises(
                            Exception,
                            match="Severance statutory classification",
                        ) as caught:
                            await invoke()

                        assert not isinstance(
                            caught.value, AssertionError
                        ), name

                        forbidden_lookup.assert_not_called()

                    assert await financial_counts() == counts_before, (
                        f"{name}: financial side-effect detected"
                    )

                    print(
                        f"{name} = FAIL-CLOSED / ZERO FINANCIAL WRITES",
                        flush=True,
                    )

                # Direct payslip-source access must also reject
                # severance, even without calling generation.
                from app.services.payroll_payslip_service import (
                    get_payslip_generation_sources,
                )

                with pytest.raises(
                    Exception,
                    match="Severance statutory classification",
                ):
                    await get_payslip_generation_sources(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=calculation.id,
                    )

                assert await financial_counts() == counts_before

                print(
                    "DIRECT PAYSLIP SOURCE = FAIL-CLOSED",
                    flush=True,
                )


                # C4-C2-B PERSISTED RESULT NEGATIVE E2E
                # Historical records are deliberately inserted
                # directly on the disposable PG clone. This is
                # NOT an approved statutory calculation route.
                from unittest.mock import patch

                from app.models.payroll_statutory import (
                    PayrollStatutoryResult,
                )
                from app.models.payroll_deduction_result import (
                    PayrollDeductionResult,
                )
                from app.models.journal_entry import JournalEntry
                from app.services.payroll_deduction_accounting_service import (
                    generate_and_post_payroll_deduction_journal,
                    PayrollDeductionAccountingConflictError,
                )

                legacy_statutory = PayrollStatutoryResult(
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                    currency_code="UAH",
                    gross_amount=Decimal("13040.00"),
                    employee_withholding_amount=Decimal("0.00"),
                    employer_contribution_amount=Decimal("0.00"),
                    net_amount=Decimal("13040.00"),
                    calculated_by=user.id,
                    calculated_at=datetime.now(UTC),
                )
                db.add(legacy_statutory)
                await db.flush()

                legacy_deduction = PayrollDeductionResult(
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                    payroll_statutory_result_id=legacy_statutory.id,
                    employment_contract_id=contract.id,
                    statutory_net_amount=Decimal("13040.00"),
                    deduction_amount=Decimal("0.00"),
                    final_payable_amount=Decimal("13040.00"),
                    currency_code="UAH",
                    calculated_by=user.id,
                    calculated_at=datetime.now(UTC),
                )
                db.add(legacy_deduction)
                await db.flush()

                assert legacy_deduction.id is not None
                assert (
                    legacy_deduction.payroll_statutory_result_id
                    == legacy_statutory.id
                )
                assert (
                    Decimal(legacy_deduction.deduction_amount)
                    == Decimal("0.00")
                )

                journal_count_before = await db.scalar(
                    select(func.count(JournalEntry.id)).where(
                        JournalEntry.company_id == company.id,
                    )
                )

                # The stored deduction result must be loaded
                # normally. The existing-journal lookup must
                # NEVER be reached, even if an old journal
                # might otherwise be returned.
                with patch(
                    "app.services.payroll_deduction_accounting_service."
                    "get_payroll_deduction_journal",
                    side_effect=AssertionError(
                        "Existing journal lookup bypassed "
                        "severance classification"
                    ),
                ) as forbidden_journal_lookup:
                    with pytest.raises(
                        PayrollDeductionAccountingConflictError,
                        match="Severance statutory classification",
                    ):
                        await generate_and_post_payroll_deduction_journal(
                            db,
                            company_id=company.id,
                            payroll_deduction_result_id=legacy_deduction.id,
                            created_by=user.id,
                        )

                    forbidden_journal_lookup.assert_not_called()

                journal_count_after = await db.scalar(
                    select(func.count(JournalEntry.id)).where(
                        JournalEntry.company_id == company.id,
                    )
                )

                assert (
                    journal_count_after
                    == journal_count_before
                ), "Unexpected accounting journal write"

                assert (
                    Decimal(legacy_statutory.gross_amount)
                    == Decimal("13040.00")
                )
                assert (
                    Decimal(legacy_deduction.final_payable_amount)
                    == Decimal("13040.00")
                )

                # Existing-result returns must remain blocked,
                # not merely creation of new results.
                from app.services.payroll_statutory_service import (
                    get_statutory_result_for_calculation,
                )
                from app.services.payroll_deduction_result_service import (
                    get_payroll_deduction_result,
                )

                assert (
                    await get_statutory_result_for_calculation(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=calculation.id,
                    )
                ).id == legacy_statutory.id

                assert (
                    await get_payroll_deduction_result(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=calculation.id,
                    )
                ).id == legacy_deduction.id

                with pytest.raises(
                    PayrollStatutoryValidationError,
                    match="Severance statutory classification",
                ):
                    await calculate_payroll_statutory_result(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=calculation.id,
                        actor_user_id=user.id,
                    )

                from app.services.payroll_deduction_result_service import (
                    calculate_payroll_deduction_result,
                    PayrollDeductionResultConflictError,
                )

                with pytest.raises(
                    PayrollDeductionResultConflictError,
                    match="Severance statutory classification",
                ):
                    await calculate_payroll_deduction_result(
                        db,
                        company_id=company.id,
                        payroll_calculation_id=calculation.id,
                        calculated_by=user.id,
                    )

                assert (
                    await db.scalar(
                        select(func.count(JournalEntry.id)).where(
                            JournalEntry.company_id == company.id,
                        )
                    )
                    == journal_count_before
                )

                print(
                    "PERSISTED STATUTORY RESULT = PRESENT",
                    flush=True,
                )
                print(
                    "PERSISTED ZERO-DEDUCTION RESULT = PRESENT",
                    flush=True,
                )
                print(
                    "DEDUCTION ACCOUNTING = FAIL-CLOSED",
                    flush=True,
                )
                print(
                    "EXISTING STATUTORY/DEDUCTION RETURNS = BLOCKED",
                    flush=True,
                )
                print(
                    "ACCOUNTING JOURNAL WRITES = 0",
                    flush=True,
                )


                # C5-C1 PAYMENT NEGATIVE E2E
                # All fixtures below exist ONLY in the cloned
                # PostgreSQL database and are rolled back.
                from app.models.account import Account
                from app.models.bank_account import BankAccount
                from app.models.employee import Employee
                from app.models.payroll_disbursement import (
                    PayrollDisbursement,
                )
                from app.services.payroll_disbursement_service import (
                    create_payroll_disbursement,
                    generate_and_post_payroll_disbursement_journal,
                    PayrollDisbursementSourceStateError,
                )

                payment_gl = Account(
                    company_id=company.id,
                    code="311",
                    name="Severance negative E2E bank GL",
                    account_type="asset",
                    normal_balance="debit",
                    is_postable=True,
                    is_active=True,
                    is_system=True,
                )
                db.add(payment_gl)
                await db.flush()

                payment_bank = BankAccount(
                    company_id=company.id,
                    name="Severance E2E bank",
                    account_number="SEVERANCE-C5-C1",
                    currency_code="UAH",
                    accounting_account_id=payment_gl.id,
                    is_active=True,
                )
                db.add(payment_bank)
                await db.flush()

                employee_for_payment = await db.get(
                    Employee, contract.employee_id
                )
                assert employee_for_payment is not None

                payment_count_before = await db.scalar(
                    select(func.count(PayrollDisbursement.id)).where(
                        PayrollDisbursement.company_id == company.id,
                        PayrollDisbursement.payroll_calculation_id
                        == calculation.id,
                    )
                )

                # Test the real guard before idempotency lookup.
                with patch(
                    "app.services.payroll_disbursement_service."
                    "get_payroll_disbursement",
                    side_effect=AssertionError(
                        "Payment existing-result lookup was reached"
                    ),
                ) as payment_lookup:
                    with pytest.raises(
                        PayrollDisbursementSourceStateError,
                        match="Severance statutory classification",
                    ):
                        await create_payroll_disbursement(
                            db,
                            company_id=company.id,
                            payroll_calculation_id=calculation.id,
                            bank_account_id=payment_bank.id,
                            payment_date=date(2026, 9, 30),
                            created_by=user.id,
                        )

                    payment_lookup.assert_not_called()

                assert await db.scalar(
                    select(func.count(PayrollDisbursement.id)).where(
                        PayrollDisbursement.company_id == company.id,
                        PayrollDisbursement.payroll_calculation_id
                        == calculation.id,
                    )
                ) == payment_count_before

                print(
                    "CREATE PAYMENT = FAIL-CLOSED / ZERO WRITES",
                    flush=True,
                )

                # Legacy payout: simulate an existing payment
                # persisted before the new severance guard.
                # This is deliberately NOT a service call.
                legacy_payment = PayrollDisbursement(
                    company_id=company.id,
                    request_key="severance-c5-existing-payment",
                    payroll_calculation_id=calculation.id,
                    employee_id=employee_for_payment.id,
                    bank_account_id=payment_bank.id,
                    employee_iban_snapshot=(
                        "UA123456789012345678901234567"
                    ),
                    amount=Decimal("100.00"),
                    currency_code="UAH",
                    payment_date=date(2026, 9, 30),
                    created_by=user.id,
                )
                db.add(legacy_payment)
                await db.flush()

                assert legacy_payment.id is not None
                assert (
                    legacy_payment.payroll_calculation_id
                    == calculation.id
                )

                journal_count_before_payment = await db.scalar(
                    select(func.count(JournalEntry.id)).where(
                        JournalEntry.company_id == company.id,
                    )
                )

                # Existing-journal lookup must not happen.
                # This tests the idempotent-return boundary.
                with patch(
                    "app.services.payroll_disbursement_service."
                    "get_payroll_disbursement_journal",
                    side_effect=AssertionError(
                        "Payment journal existing-result "
                        "lookup was reached"
                    ),
                ) as journal_lookup:
                    with pytest.raises(
                        PayrollDisbursementSourceStateError,
                        match="Severance statutory classification",
                    ):
                        await generate_and_post_payroll_disbursement_journal(
                            db,
                            company_id=company.id,
                            payroll_disbursement_id=legacy_payment.id,
                            created_by=user.id,
                        )

                    journal_lookup.assert_not_called()

                assert await db.scalar(
                    select(func.count(JournalEntry.id)).where(
                        JournalEntry.company_id == company.id,
                    )
                ) == journal_count_before_payment

                assert (
                    await db.get(
                        PayrollDisbursement,
                        legacy_payment.id,
                    )
                ) is not None

                print(
                    "PERSISTED PAYMENT = VERIFIED",
                    flush=True,
                )
                print(
                    "POST PAYMENT JOURNAL = FAIL-CLOSED",
                    flush=True,
                )
                print(
                    "PAYMENT JOURNAL WRITES = 0",
                    flush=True,
                )

                # NEGATIVE 1: finalized periods must still reject
                # ordinary source-creation preflight.
                with pytest.raises(
                    SeverancePreflightError,
                    match="open company payroll period",
                ):
                    await preflight_termination_severance(
                        db,
                        company_id=company.id,
                        payroll_input_id=payroll_input.id,
                    )

                # NEGATIVE 2: the explicit finalized validation is
                # allowed only because an immutable source exists.
                finalized_preflight = (
                    await preflight_termination_severance(
                        db,
                        company_id=company.id,
                        payroll_input_id=payroll_input.id,
                        allow_finalized_source=True,
                    )
                )
                assert (
                    finalized_preflight.termination_event_id
                    == event.id
                )

                with pytest.raises(
                    SeverancePreflightError,
                    match="existing immutable severance source",
                ):
                    await preflight_termination_severance(
                        db,
                        company_id=company.id,
                        payroll_input_id=payroll_input.id + 999999,
                        allow_finalized_source=True,
                    )

                # NEGATIVE 3: simulate a new documented historical
                # source revision following an existing calculation.
                # This directly tests stale-calculation detection;
                # it is NOT an approved finalized-period correction API.
                corrected_source = PayrollTerminationSeverance(
                    company_id=company.id,
                    payroll_input_id=payroll_input.id,
                    employment_contract_id=contract.id,
                    termination_event_id=event.id,
                    revision=2,
                    supersedes_id=source.id,
                    request_key=f"severance-correction-{token}",
                    termination_date=source.termination_date,
                    order_number=source.order_number,
                    policy_ground=source.policy_ground,
                    policy_category=source.policy_category,
                    labour_code_provision=source.labour_code_provision,
                    source_reference=source.source_reference,
                    multiplier=source.multiplier,
                    basis_as_of=source.basis_as_of,
                    basis_reference="Revised documented average earnings",
                    base_amount=Decimal("13000.00"),
                    gross_amount=Decimal("13000.00"),
                    currency_code="UAH",
                    created_by=user.id,
                )
                db.add(corrected_source)
                await db.flush()

                with pytest.raises(
                    PayrollCalculationConflictError,
                    match="Severance changed after payroll calculation",
                ):
                    await calculate_payroll_input(
                        db,
                        company_id=company.id,
                        payroll_input_id=payroll_input.id,
                        calculated_by=user.id,
                    )

                # No second calculation or replacement severance
                # line may appear after the conflict.
                calculations_after = await db.scalar(
                    select(func.count(PayrollCalculation.id)).where(
                        PayrollCalculation.company_id == company.id,
                        PayrollCalculation.payroll_input_id
                        == payroll_input.id,
                    )
                )
                assert calculations_after == 1

                original_severance_lines = (
                    await db.scalars(
                        select(PayrollCalculationLine).where(
                            PayrollCalculationLine.company_id
                            == company.id,
                            PayrollCalculationLine.payroll_calculation_id
                            == calculation.id,
                            PayrollCalculationLine.line_type
                            == "severance",
                        )
                    )
                ).all()

                assert len(original_severance_lines) == 1
                assert (
                    original_severance_lines[0]
                    .source_termination_severance_id == source.id
                )
                assert (
                    Decimal(original_severance_lines[0].amount)
                    == Decimal("12000.00")
                )
                assert (
                    Decimal(calculation.gross_amount)
                    == Decimal("13040.00")
                )

        print("FULL calculate_payroll_input = PASS")
        print("SALARY LINE = 400.00 UAH")
        print("SEVERANCE LINE = 12000.00 UAH")
        print("MINIMUM WAGE TOP-UP = 640.00 UAH")
        print("GROSS = 13040.00 UAH")
        print("REPEATED CALCULATION = IDEMPOTENT")
        print("STATUTORY MISCLASSIFICATION = BLOCKED")
        print("POSTGRESQL SKIPS = 0")

    finally:
        await engine.dispose()
