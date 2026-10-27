from app.services.account_types import AccountType, AccountNormalBalance
import json
import os
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_source_locking import sources
from payroll_verified_evidence_helper import verified_evidence
from app.models.account import Account, AccountType, AccountNormalBalance
from app.models.bank_account import BankAccount
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.payroll import PayrollPeriod
from app.models.payroll_statutory import PayrollStatutoryComponent as Component
from app.models.payroll_tax import PayrollEmployeeTaxProfile, PayrollStatutoryBaseRule
from app.schemas.time_attendance import AttendanceCreate
from app.services.time_attendance_service import create_attendance
from app.services.payroll_automatic_advance_service import create_automatic_payroll_advance
from app.services.payroll_advance_service import PayrollAdvanceError
from app.services.payroll_statutory_service import create_statutory_rate, resolve_statutory_rate, PayrollStatutoryError

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]




@pytest.mark.asyncio
async def test_dated_two_rate_real_postgresql(employee_engine):
    from app.services.payroll_calculation_service import calculate_payroll_input
    from app.models.payroll import (
        PayrollInput,
        PayrollInputSalarySlice,
        PayrollCalculationLine,
    )
    from app.models.payroll_statutory import (
        PayrollStatutoryRate,
        PayrollStatutoryComponent,
    )
    from app.services.payroll_dated_component_verified import (
        preview_verified_dated_salary_tax,
    )
    from app.services.payroll_dated_income_source_service import (
        load_verified_dated_salary_allocation,
    )

    component = PayrollStatutoryComponent.PERSONAL_INCOME_TAX

    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        f, employment, _ = await sources(db)

        period = PayrollPeriod(
            company_id=f["company"],
            year=2026,
            month=9,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 30),
            status='finalized',
            created_by=f["actor"],
            finalized_by=f['actor'],
            finalized_at=datetime(2026, 10, 1, 12, 0, tzinfo=ZoneInfo('Europe/Kyiv')),
        )
        db.add(period)
        await db.flush()

        existing_salary = await db.scalar(
            select(EmployeeSalaryRate)
            .where(
                EmployeeSalaryRate.company_id == f["company"],
                EmployeeSalaryRate.employment_contract_id == employment.id,
                EmployeeSalaryRate.effective_from <= date(2026, 9, 1),
            )
            .order_by(EmployeeSalaryRate.effective_from.desc())
        )
        assert existing_salary is not None, (
            "STOP: Source fixture has no verified salary rate"
        )

        payroll_input = PayrollInput(
                            monthly_norm_minutes=10560,
            company_id=f["company"],
            payroll_period_id=period.id,
            employment_contract_id=employment.id,
            scheduled_minutes=6000,
            worked_minutes=6000,
            leave_days=0,
            sick_days=0,
            manual_adjustment_amount=Decimal("0.00"),
            created_by=f["actor"],
        )
        db.add(payroll_input)
        await db.flush()

        for start, end in (
            (date(2026, 9, 1), date(2026, 9, 14)),
            (date(2026, 9, 15), date(2026, 9, 30)),
        ):
            db.add(
                PayrollInputSalarySlice(
                    company_id=f["company"],
                    payroll_input_id=payroll_input.id,
                    salary_rate_id=existing_salary.id,
                    salary_rate_type=existing_salary.rate_type,
                    salary_rate_amount=existing_salary.amount,
                    currency_code="UAH",
                    effective_from=start,
                    effective_to=end,
                    scheduled_minutes=3000,
                    worked_minutes=3000,
                )
            )

        await db.flush()

        first_rate = await create_statutory_rate(
            db,
            company_id=f["company"],
            component=component,
            rate=Decimal("0.18"),
            effective_from=date(2026, 9, 1),
            effective_to=date(2026, 9, 14),
            actor_user_id=f["actor"],
        )

        second_rate = await create_statutory_rate(
            db,
            company_id=f["company"],
            component=component,
            rate=Decimal("0.19"),
            effective_from=date(2026, 9, 15),
            effective_to=date(2026, 9, 30),
            actor_user_id=f["actor"],
        )

        await db.flush()

        calculation = await calculate_payroll_input(
            db,
            company_id=f["company"],
            payroll_input_id=payroll_input.id,
            calculated_by=f["actor"],
        )
        await db.flush()

        salary_lines = list(
            (
                await db.scalars(
                    select(PayrollCalculationLine)
                    .where(
                        PayrollCalculationLine.company_id
                        == f["company"],
                        PayrollCalculationLine.payroll_calculation_id
                        == calculation.id,
                        PayrollCalculationLine.line_type == "salary",
                    )
                    .order_by(
                        PayrollCalculationLine.source_effective_from
                    )
                )
            ).all()
        )

        assert len(salary_lines) == 2, (
            "STOP: Expected two actual dated salary lines"
        )

        assert [
            (line.source_effective_from, line.source_effective_to)
            for line in salary_lines
        ] == [
            (date(2026, 9, 1), date(2026, 9, 14)),
            (date(2026, 9, 15), date(2026, 9, 30)),
        ]

        allocations = await load_verified_dated_salary_allocation(
            db,
            company_id=f["company"],
            payroll_calculation_id=calculation.id,
            component=component,
        )

        assert len(allocations) == 2
        assert [row.source_rate_id for row in allocations] == [
            first_rate.id,
            second_rate.id,
        ]

        print("13.10 TWO DATED PG SOURCES = PASS")
        print("13.10 TWO DISTINCT RATE IDS = PASS")

        result = await preview_verified_dated_salary_tax(
            db,
            company_id=f["company"],
            payroll_calculation_id=calculation.id,
            component=component,
        )

        assert len(result) == 2

        by_id = {row.source_rate_id: row for row in result}

        assert set(by_id) == {first_rate.id, second_rate.id}
        assert by_id[first_rate.id].rate == Decimal("0.18")
        assert by_id[second_rate.id].rate == Decimal("0.19")

        assert sum(
            (row.base_amount for row in result),
            Decimal("0.00"),
        ) == Decimal(calculation.gross_amount)

        from decimal import ROUND_HALF_UP

        for row in result:
            expected = (
                row.base_amount * row.rate
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            assert row.amount == expected

        print("13.10 EXACT TWO-RATE TAX AMOUNTS = PASS")

        retry = await preview_verified_dated_salary_tax(
            db,
            company_id=f["company"],
            payroll_calculation_id=calculation.id,
            component=component,
        )
        assert retry == result

        print("13.10 POSITIVE RETRY = PASS")

        # F2-C2 THREE-COMPONENT REAL POSTGRESQL
        from app.models.payroll_statutory import (
            PayrollStatutoryResult as F2C2Result,
            PayrollStatutoryResultLine as F2C2ResultLine,
        )
        from sqlalchemy import func as f2c2_func

        async def assert_f2c2_no_statutory_result():
            headers = await db.scalar(
                select(f2c2_func.count(F2C2Result.id)).where(
                    F2C2Result.company_id == f["company"],
                    F2C2Result.payroll_calculation_id == calculation.id,
                )
            )
            assert headers == 0

            lines_count = await db.scalar(
                select(f2c2_func.count(F2C2ResultLine.id))
                .join(
                    F2C2Result,
                    F2C2Result.id
                    == F2C2ResultLine.payroll_statutory_result_id,
                )
                .where(
                    F2C2ResultLine.company_id == f["company"],
                    F2C2Result.company_id == f["company"],
                    F2C2Result.payroll_calculation_id == calculation.id,
                )
            )
            assert lines_count == 0
        from app.services.payroll_dated_statutory_verified_plan import (
            build_verified_dated_salary_statutory_plan,
        )
        from app.services.payroll_dated_component_verified import (
            VerifiedDatedComponentError,
        )
        from app.services.payroll_statutory_service import (
            PayrollStatutoryError,
        )
        from decimal import ROUND_HALF_UP

        military_rate = await create_statutory_rate(
            db,
            company_id=f["company"],
            component=PayrollStatutoryComponent.MILITARY_LEVY,
            rate=Decimal("0.05"),
            effective_from=date(2026, 9, 1),
            effective_to=date(2026, 9, 30),
            actor_user_id=f["actor"],
        )

        usc_rate = await create_statutory_rate(
            db,
            company_id=f["company"],
            component=PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION,
            rate=Decimal("0.22"),
            effective_from=date(2026, 9, 1),
            effective_to=date(2026, 9, 30),
            actor_user_id=f["actor"],
        )

        await db.flush()

        plan = await build_verified_dated_salary_statutory_plan(
            db,
            company_id=f["company"],
            payroll_calculation_id=calculation.id,
        )

        assert plan.currency_code == "UAH"
        assert plan.gross_amount == Decimal(calculation.gross_amount)

        component_lines = {}
        for line in plan.lines:
            component_lines.setdefault(line.component, []).append(line)

        pit = component_lines["personal_income_tax"]
        military = component_lines["military_levy"]
        usc = component_lines["unified_social_contribution"]

        assert len(pit) == 2
        assert len(military) == 1
        assert len(usc) == 1
        assert len(plan.lines) == 4

        assert {
            row.source_rate_id for row in pit
        } == {first_rate.id, second_rate.id}

        assert military[0].source_rate_id == military_rate.id
        assert usc[0].source_rate_id == usc_rate.id

        assert sum(
            (line.base_amount for line in pit),
            Decimal("0.00"),
        ) == plan.gross_amount

        assert military[0].base_amount == plan.gross_amount
        assert usc[0].base_amount == plan.gross_amount

        for line in plan.lines:
            expected_amount = (
                line.base_amount * line.rate
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            assert line.amount == expected_amount
            assert line.source_rate_id > 0
            assert line.source_line_ids

        withholding = sum(
            (
                line.amount
                for line in plan.lines
                if line.component in (
                    "personal_income_tax",
                    "military_levy",
                )
            ),
            Decimal("0.00"),
        )

        contributions = sum(
            (
                line.amount
                for line in plan.lines
                if line.component == "unified_social_contribution"
            ),
            Decimal("0.00"),
        )

        assert plan.employee_withholding_amount == withholding
        assert plan.employer_contribution_amount == contributions
        assert plan.net_amount == plan.gross_amount - withholding
        assert plan.net_amount >= 0

        print("F2-C2 THREE COMPONENTS = PASS")
        print("F2-C2 FOUR VERIFIED STATUTORY LINES = PASS")
        print("F2-C2 PIT TWO RATES = PASS")
        print("F2-C2 MILITARY LEVY = PASS")
        print("F2-C2 USC = PASS")
        print("F2-C2 GROSS/WITHHOLDING/NET = PASS")
        print("F2-C2 EMPLOYER CONTRIBUTION = PASS")

        retry_plan = await build_verified_dated_salary_statutory_plan(
            db,
            company_id=f["company"],
            payroll_calculation_id=calculation.id,
        )

        assert retry_plan == plan
        print("F2-C2 IDEMPOTENT READ-ONLY RETRY = PASS")

        await assert_f2c2_no_statutory_result()
        print("F2-C2 ZERO STATUTORY WRITES = PASS")

        # Negative scenario: make the USC rate start on 2 September.
        # The first day must no longer have an applicable USC rate.
        savepoint = await db.begin_nested()

        try:
            usc_rate.effective_from = date(2026, 9, 2)
            await db.flush()

            with pytest.raises(
                (VerifiedDatedComponentError, PayrollStatutoryError)
            ):
                await build_verified_dated_salary_statutory_plan(
                    db,
                    company_id=f["company"],
                    payroll_calculation_id=calculation.id,
                )

            await assert_f2c2_no_statutory_result()

            print("F2-C2 INVALID USC RATE REJECTED = PASS")
            print("F2-C2 NEGATIVE ZERO PARTIAL WRITES = PASS")

        finally:
            if savepoint.is_active:
                await savepoint.rollback()

        await db.refresh(usc_rate)

        restored = await build_verified_dated_salary_statutory_plan(
            db,
            company_id=f["company"],
            payroll_calculation_id=calculation.id,
        )

        assert restored == plan
        await assert_f2c2_no_statutory_result()

        print("F2-C2 ROLLBACK AND POSITIVE RETRY = PASS")
        print("F2-C2 REAL THREE-COMPONENT PG E2E = PASS")

        # F3-A REAL ATOMIC WRITER PERSISTENCE E2E
        from sqlalchemy import func as f3a_func
        from app.models.payroll_statutory import (
            PayrollStatutoryResult as F3AResult,
            PayrollStatutoryResultLine as F3ALine,
            PayrollStatutoryLineSource as F3ASource,
        )
        from app.services.payroll_dated_statutory_atomic_writer import (
            write_verified_dated_salary_statutory_result,
            DatedStatutoryAtomicWriterError,
        )

        async def f3a_counts():
            headers = await db.scalar(
                select(f3a_func.count(F3AResult.id)).where(
                    F3AResult.company_id == f["company"],
                    F3AResult.payroll_calculation_id == calculation.id,
                )
            )

            lines_count = await db.scalar(
                select(f3a_func.count(F3ALine.id))
                .join(
                    F3AResult,
                    F3AResult.id
                    == F3ALine.payroll_statutory_result_id,
                )
                .where(
                    F3AResult.company_id == f["company"],
                    F3AResult.payroll_calculation_id == calculation.id,
                )
            )

            links = await db.scalar(
                select(f3a_func.count(F3ASource.id))
                .join(
                    F3ALine,
                    F3ALine.id == F3ASource.statutory_line_id,
                )
                .join(
                    F3AResult,
                    F3AResult.id
                    == F3ALine.payroll_statutory_result_id,
                )
                .where(
                    F3AResult.company_id == f["company"],
                    F3AResult.payroll_calculation_id == calculation.id,
                )
            )

            return headers, lines_count, links

        assert await f3a_counts() == (0, 0, 0)

        # F3-B3-B-R2 STABLE INPUT IDENTIFIERS
        f3b3_calculation_id = int(calculation.id)
        f3b3_company_id = int(f["company"])
        f3b3_actor_id = int(f["actor"])
        assert all(x > 0 for x in (
            f3b3_calculation_id,
            f3b3_company_id,
            f3b3_actor_id,
        ))
        print("F3-B3-B STABLE IDS CAPTURED = PASS", flush=True)

        # F3-B3-B ISOLATED FIXTURE COMMIT
        from sqlalchemy import text as f3b3_text

        f3b3_context = (
            await db.execute(
                f3b3_text(
                    "SELECT current_database(), current_schema()"
                )
            )
        ).one()

        assert "_copy_" in f3b3_context[0], (
            "STOP: Concurrency fixture commit outside isolated copy"
        )
        assert f3b3_context[1].startswith("test_"), (
            "STOP: Concurrency fixture commit outside test schema"
        )

        # Persist authentic payroll input and rates, not statutory
        # results. Independent PostgreSQL connections must see them.
        assert await f3a_counts() == (0, 0, 0)
        await db.commit()

        print("F3-B3-B COMMITTED INPUT FIXTURE = PASS", flush=True)

        outer_savepoint = await db.begin_nested()

        try:
            saved_header = await write_verified_dated_salary_statutory_result(
                db,
                company_id=f["company"],
                payroll_calculation_id=calculation.id,
                actor_user_id=f["actor"],
            )

            await db.flush()

            assert await f3a_counts() == (1, 4, 6)

            saved_lines = list((
                await db.scalars(
                    select(F3ALine)
                    .where(
                        F3ALine.company_id == f["company"],
                        F3ALine.payroll_statutory_result_id
                        == saved_header.id,
                    )
                    .order_by(F3ALine.line_no)
                )
            ).all())

            assert len(saved_lines) == 4
            assert [line.line_no for line in saved_lines] == [1, 2, 3, 4]

            assert [line.component for line in saved_lines] == [
                planned.component for planned in plan.lines
            ]

            assert [
                line.source_rate_id for line in saved_lines
            ] == [
                planned.source_rate_id for planned in plan.lines
            ]

            assert saved_header.gross_amount == plan.gross_amount
            assert (
                saved_header.employee_withholding_amount
                == plan.employee_withholding_amount
            )
            assert (
                saved_header.employer_contribution_amount
                == plan.employer_contribution_amount
            )
            assert saved_header.net_amount == plan.net_amount

            print("F3-A HEADER AND FOUR LINES = PASS")
            print("F3-A EXACT AMOUNTS AND RATE IDS = PASS")

            all_source_ids = {line.id for line in salary_lines}

            for actual_line, planned in zip(
                saved_lines, plan.lines, strict=True
            ):
                links = list((
                    await db.scalars(
                        select(F3ASource)
                        .where(
                            F3ASource.company_id == f["company"],
                            F3ASource.statutory_line_id
                            == actual_line.id,
                        )
                        .order_by(F3ASource.payroll_calculation_line_id)
                    )
                ).all())

                assert {
                    link.payroll_calculation_line_id
                    for link in links
                } == set(planned.source_line_ids)

                assert {
                    link.payroll_calculation_line_id
                    for link in links
                } <= all_source_ids

                assert sum(
                    (link.source_amount for link in links),
                    Decimal("0.00"),
                ) == actual_line.base_amount

                for link in links:
                    source = next(
                        item for item in salary_lines
                        if item.id == link.payroll_calculation_line_id
                    )

                    assert link.source_amount == source.amount
                    assert (
                        link.income_effective_from
                        == source.source_effective_from
                    )
                    assert (
                        link.income_effective_to
                        == source.source_effective_to
                    )

            print("F3-A SIX AUTHENTIC SOURCE LINKS = PASS")
            print("F3-A SOURCE AMOUNT/DATE RECONCILIATION = PASS")

            # A second request must not overwrite the saved result.
            with pytest.raises(
                DatedStatutoryAtomicWriterError,
                match="Existing statutory result",
            ):
                await write_verified_dated_salary_statutory_result(
                    db,
                    company_id=f["company"],
                    payroll_calculation_id=calculation.id,
                    actor_user_id=f["actor"],
                )

            assert await f3a_counts() == (1, 4, 6)
            print("F3-A DUPLICATE WRITE REJECTED = PASS")
            print("F3-A SAVED RESULT PRESERVED = PASS")

            # F3-B2-B CLEAN FIVE-GUARD IMMUTABILITY E2E
            from sqlalchemy import text as f3b_clean_text
            from sqlalchemy.exc import DBAPIError as F3BCleanDBError

            schema_info = (
                await db.execute(
                    f3b_clean_text("""
                        SELECT current_schema(), current_database()
                    """)
                )
            ).one()

            fixture_schema, current_db = schema_info

            assert fixture_schema.startswith("test_"), (
                "STOP: Must run inside isolated pytest schema"
            )
            assert "_copy_" in current_db, (
                "STOP: Must run inside isolated PostgreSQL copy"
            )

            print("F3-B2-B ISOLATED TEST SCHEMA = PASS", flush=True)

            tables = (
                "payroll_statutory_results",
                "payroll_statutory_result_lines",
                "payroll_statutory_line_sources",
                "payroll_calculation_lines",
                "payroll_statutory_rates",
            )

            for table_name in tables:
                fixture_oid, public_oid = (
                    await db.execute(
                        f3b_clean_text("""
                            SELECT
                                to_regclass(:fixture)::oid,
                                to_regclass(:public)::oid
                        """),
                        {
                            "fixture": table_name,
                            "public": "public." + table_name,
                        },
                    )
                ).one()
                assert fixture_oid and public_oid
                assert fixture_oid != public_oid

            header_id = saved_header.id

            def clean_sqlstate(error):
                found = set()
                current = error
                while current is not None and id(current) not in found:
                    found.add(id(current))
                    value = (
                        getattr(current, "sqlstate", None)
                        or getattr(current, "pgcode", None)
                    )
                    if value:
                        return value
                    current = (
                        getattr(current, "orig", None)
                        or getattr(current, "__cause__", None)
                    )
                return None

            async def clean_counts():
                return tuple((
                    await db.execute(
                        f3b_clean_text("""
                            SELECT
                                count(DISTINCT h.id),
                                count(DISTINCT l.id),
                                count(DISTINCT s.id)
                            FROM payroll_statutory_results h
                            LEFT JOIN payroll_statutory_result_lines l
                              ON l.company_id = h.company_id
                             AND l.payroll_statutory_result_id = h.id
                            LEFT JOIN payroll_statutory_line_sources s
                              ON s.company_id = l.company_id
                             AND s.statutory_line_id = l.id
                            WHERE h.id = :header_id
                        """),
                        {"header_id": header_id},
                    )
                ).one())

            assert await clean_counts() == (1, 4, 6)

            source_row = (
                await db.execute(
                    f3b_clean_text("""
                        SELECT
                            s.id AS link_id,
                            s.payroll_calculation_line_id AS salary_id,
                            l.id AS statutory_line_id,
                            l.source_rate_id AS rate_id
                        FROM payroll_statutory_result_lines l
                        JOIN payroll_statutory_line_sources s
                          ON s.company_id = l.company_id
                         AND s.statutory_line_id = l.id
                        WHERE l.payroll_statutory_result_id = :header_id
                          AND l.company_id = :company_id
                        ORDER BY s.id
                        LIMIT 1
                    """),
                    {
                        "header_id": header_id,
                        "company_id": f["company"],
                    },
                )
            ).one()

            assert all(value is not None for value in source_row)

            targets = (
                (
                    "header",
                    "payroll_statutory_results",
                    header_id,
                ),
                (
                    "statutory_line",
                    "payroll_statutory_result_lines",
                    source_row.statutory_line_id,
                ),
                (
                    "source_link",
                    "payroll_statutory_line_sources",
                    source_row.link_id,
                ),
                (
                    "salary_source",
                    "payroll_calculation_lines",
                    source_row.salary_id,
                ),
                (
                    "statutory_rate",
                    "payroll_statutory_rates",
                    source_row.rate_id,
                ),
            )

            # The complete row image must stay identical after every
            # rejected operation, not only the record counts.
            async def clean_snapshot():
                output = {}
                for label, table_name, row_id in targets:
                    value = await db.scalar(
                        f3b_clean_text(
                            "SELECT row_to_json(t)::text "
                            "FROM (SELECT * FROM " + table_name +
                            " WHERE id = :id) t"
                        ),
                        {"id": row_id},
                    )
                    assert value is not None, (
                        f"STOP: Missing protected row: {label}"
                    )
                    output[label] = value
                return output

            baseline = await clean_snapshot()

            # DDL is temporary and applied ONLY in the test schema.
            # The production public triggers and migration are unchanged.
            ddl_savepoint = await db.begin_nested()

            trigger_specs = (
                (
                    "trg_dated_statutory_header_immutable",
                    "payroll_statutory_results",
                ),
                (
                    "trg_dated_statutory_line_immutable",
                    "payroll_statutory_result_lines",
                ),
                (
                    "trg_dated_statutory_source_link_immutable",
                    "payroll_statutory_line_sources",
                ),
                (
                    "trg_dated_statutory_salary_source_immutable",
                    "payroll_calculation_lines",
                ),
                (
                    "trg_dated_statutory_rate_immutable",
                    "payroll_statutory_rates",
                ),
            )

            try:
                for trigger_name, table_name in trigger_specs:
                    existing = await db.scalar(
                        f3b_clean_text("""
                            SELECT count(*)
                            FROM pg_trigger
                            WHERE tgrelid = to_regclass(:table_name)
                              AND tgname = :trigger_name
                              AND NOT tgisinternal
                        """),
                        {
                            "table_name": table_name,
                            "trigger_name": trigger_name,
                        },
                    )
                    assert existing == 0, (
                        "STOP: Fixture schema already has guard: "
                        + trigger_name
                    )

                    await db.execute(
                        f3b_clean_text(
                            "CREATE TRIGGER " + trigger_name +
                            " BEFORE UPDATE OR DELETE ON " +
                            table_name +
                            " FOR EACH ROW EXECUTE FUNCTION "
                            "public.erp_guard_dated_statutory_evidence()"
                        )
                    )

                print(
                    "F3-B2-B FIVE FIXTURE-SCHEMA GUARDS = PASS",
                    flush=True,
                )

                for label, table_name, row_id in targets:
                    count = await db.scalar(
                        f3b_clean_text(
                            "SELECT count(*) FROM " + table_name +
                            " WHERE id = :id"
                        ),
                        {"id": row_id},
                    )
                    assert count == 1, (
                        f"STOP: Missing target {label}"
                    )

                    for operation in ("UPDATE", "DELETE"):
                        name = label + "_" + operation.lower()

                        if operation == "UPDATE":
                            statement = (
                                "UPDATE " + table_name +
                                " SET id = id WHERE id = :id"
                            )
                        else:
                            statement = (
                                "DELETE FROM " + table_name +
                                " WHERE id = :id"
                            )

                        mutation_savepoint = await db.begin_nested()

                        try:
                            try:
                                await db.execute(
                                    f3b_clean_text(statement),
                                    {"id": row_id},
                                )
                            except F3BCleanDBError as exc:
                                assert clean_sqlstate(exc) == "23514", (
                                    f"{name}: unexpected SQLSTATE "
                                    f"{clean_sqlstate(exc)}"
                                )
                            else:
                                raise AssertionError(
                                    f"STOP: Mutation was accepted: {name}"
                                )
                        finally:
                            if mutation_savepoint.is_active:
                                await mutation_savepoint.rollback()

                        assert await clean_counts() == (1, 4, 6), name
                        assert await clean_snapshot() == baseline, name

                        print(
                            "F3-B2-B MUTATION BLOCKED = PASS",
                            name,
                            flush=True,
                        )

                print(
                    "F3-B2-B TEN UPDATE/DELETE REJECTIONS = PASS",
                    flush=True,
                )

            finally:
                if ddl_savepoint.is_active:
                    await ddl_savepoint.rollback()

            for trigger_name, table_name in trigger_specs:
                remaining = await db.scalar(
                    f3b_clean_text("""
                        SELECT count(*)
                        FROM pg_trigger
                        WHERE tgrelid = to_regclass(:table_name)
                          AND tgname = :trigger_name
                          AND NOT tgisinternal
                    """),
                    {
                        "table_name": table_name,
                        "trigger_name": trigger_name,
                    },
                )
                assert remaining == 0, trigger_name

            assert await clean_counts() == (1, 4, 6)
            assert await clean_snapshot() == baseline

            print("F3-B2-B TEMPORARY DDL ROLLBACK = PASS", flush=True)
            print("F3-B2-B SAVED EVIDENCE PRESERVED = PASS", flush=True)
            print("F3-B2-B REAL IMMUTABILITY PG E2E = PASS", flush=True)

        finally:
            if outer_savepoint.is_active:
                await outer_savepoint.rollback()

        assert await f3a_counts() == (0, 0, 0)

        print("F3-A OUTER ROLLBACK ZERO LEAK = PASS")
        print("F3-A REAL ATOMIC WRITER PG E2E = PASS")



        from app.services.payroll_statutory_service import (
            calculate_payroll_statutory_result,
            PayrollStatutoryValidationError,
        )
        from app.models.payroll_statutory import (
            PayrollStatutoryResult,
            PayrollStatutoryResultLine,
        )
        from sqlalchemy import func

        def result_query():
            return select(func.count(PayrollStatutoryResult.id)).where(
                PayrollStatutoryResult.company_id == f["company"],
                PayrollStatutoryResult.payroll_calculation_id == calculation.id,
            )

        before = await db.scalar(result_query())
        assert before == 0

        with pytest.raises(
            PayrollStatutoryValidationError,
            match="dated allocation|rate changes|rate change",
        ):
            await calculate_payroll_statutory_result(
                db,
                company_id=f["company"],
                payroll_calculation_id=calculation.id,
                actor_user_id=f["actor"],
            )

        await db.flush()

        after = await db.scalar(result_query())
        assert after == before == 0

        orphan_lines = await db.scalar(
            select(func.count(PayrollStatutoryResultLine.id)).where(
                PayrollStatutoryResultLine.company_id == f["company"]
            )
        )
        assert orphan_lines == 0, (
            "STOP: Unexpected statutory result lines"
        )

        print("13.10-B2-D1 STATUTORY GUARD = PASS")
        print("13.10-B2-D1 NO PARTIAL RESULT = PASS")
        # F2-B2-C2 NEGATIVE PROVENANCE MUTATIONS
        from app.services.payroll_dated_component_verified import (
            VerifiedDatedComponentError,
        )
        from app.services.payroll_dated_income_allocation import (
            DatedIncomeAllocationError,
        )
        from app.services.payroll_statutory_service import (
            PayrollStatutoryNotFoundError,
            PayrollStatutoryValidationError,
        )

        expected_errors = (
            VerifiedDatedComponentError,
            DatedIncomeAllocationError,
            PayrollStatutoryNotFoundError,
            PayrollStatutoryValidationError,
        )

        async def assert_original_preview():
            verified = await preview_verified_dated_salary_tax(
                db,
                company_id=f["company"],
                payroll_calculation_id=calculation.id,
                component=component,
            )
            assert verified == result

        async def zero_statutory_results():
            assert await db.scalar(result_query()) == 0
            assert await db.scalar(
                select(func.count(PayrollStatutoryResultLine.id)).where(
                    PayrollStatutoryResultLine.company_id == f["company"]
                )
            ) == 0

        # Test ownership using a company ID that cannot own this calculation.
        with pytest.raises(expected_errors):
            await preview_verified_dated_salary_tax(
                db,
                company_id=f["company"] + 99999999,
                payroll_calculation_id=calculation.id,
                component=component,
            )
        await zero_statutory_results()
        print("F2-B2-C2 FOREIGN COMPANY = PASS")

        # Each mutation is isolated. A negative result is accepted only
        # when the production source/rate resolver rejects it.
        for case in (
            "rate_gap",
            "rate_component_substitution",
            "source_outside_period",
            "source_gross_mismatch",
            "source_missing_salary_provenance",
        ):
            savepoint = await db.begin_nested()
            try:
                if case == "rate_gap":
                    first_rate.effective_to = date(2026, 9, 13)
                elif case == "rate_component_substitution":
                    second_rate.component = (
                        PayrollStatutoryComponent.MILITARY_LEVY.value
                    )
                elif case == "source_outside_period":
                    salary_lines[0].source_effective_from = date(2026, 8, 31)
                elif case == "source_gross_mismatch":
                    salary_lines[0].amount = (
                        Decimal(salary_lines[0].amount) + Decimal("1.00")
                    )
                else:
                    salary_lines[0].source_salary_rate_id = None

                # The requested mutation must reach PostgreSQL.
                await db.flush()

                with pytest.raises(expected_errors):
                    await preview_verified_dated_salary_tax(
                        db,
                        company_id=f["company"],
                        payroll_calculation_id=calculation.id,
                        component=component,
                    )

                await zero_statutory_results()
                print("F2-B2-C2 MUTATION REJECTED = PASS", case)
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()

            await db.refresh(first_rate)
            await db.refresh(second_rate)
            for source_line in salary_lines:
                await db.refresh(source_line)

            await assert_original_preview()
            await zero_statutory_results()
            print("F2-B2-C2 RESTORE AND RETRY = PASS", case)

        print("F2-B2-C2 ZERO PARTIAL WRITES = PASS")
        await db.rollback()

        print("13.10 POSITIVE PG E2E = PASS")
        # F3-B3-B REAL TWO-SESSION CONCURRENCY E2E
        import asyncio as f3b3_asyncio
        from sqlalchemy.ext.asyncio import AsyncSession as F3B3Session
        from sqlalchemy import text as f3b3_sql

        async def f3b3_context_check(session):
            row = (
                await session.execute(
                    f3b3_sql(
                        "SELECT current_database(), "
                        "current_schema(), pg_backend_pid()"
                    )
                )
            ).one()
            assert row[0] == f3b3_context[0], (
                "STOP: Concurrency connection uses wrong database"
            )
            assert row[1] == f3b3_context[1], (
                "STOP: Concurrency connection uses wrong schema"
            )
            return row[2]

        async with F3B3Session(
            employee_engine, expire_on_commit=False
        ) as winner_session:
            async with F3B3Session(
                employee_engine, expire_on_commit=False
            ) as loser_session:
                async with F3B3Session(
                    employee_engine, expire_on_commit=False
                ) as observer_session:

                    winner_pid = await f3b3_context_check(
                        winner_session
                    )
                    loser_pid = await f3b3_context_check(
                        loser_session
                    )
                    observer_pid = await f3b3_context_check(
                        observer_session
                    )

                    assert len({
                        winner_pid, loser_pid, observer_pid
                    }) == 3, (
                        "STOP: Connections are not independent"
                    )

                    print(
                        "F3-B3-B THREE INDEPENDENT PG CONNECTIONS = PASS",
                        flush=True,
                    )

                    # Verify source calculations are visible from the
                    # independent committed transaction.
                    source_visible = await winner_session.scalar(
                        f3b3_sql("""
                            SELECT count(*)
                            FROM payroll_calculations
                            WHERE id = :calculation_id
                              AND company_id = :company_id
                        """),
                        {
                            "calculation_id": f3b3_calculation_id,
                            "company_id": f3b3_company_id,
                        },
                    )
                    assert source_visible == 1

                    holder_result = await (
                        write_verified_dated_salary_statutory_result(
                            winner_session,
                            company_id=f3b3_company_id,
                            payroll_calculation_id=f3b3_calculation_id,
                            actor_user_id=f3b3_actor_id,
                        )
                    )

                    assert holder_result.id is not None

                    print(
                        "F3-B3-B WINNER UNCOMMITTED WRITE = PASS",
                        flush=True,
                    )

                    async def contender_attempt():
                        try:
                            await (
                                write_verified_dated_salary_statutory_result(
                                    loser_session,
                                    company_id=f3b3_company_id,
                                    payroll_calculation_id=f3b3_calculation_id,
                                    actor_user_id=f3b3_actor_id,
                                )
                            )
                        except DatedStatutoryAtomicWriterError as exc:
                            await loser_session.rollback()
                            return ("rejected", str(exc))
                        else:
                            await loser_session.rollback()
                            return ("unexpected_success", "")

                    contender_task = f3b3_asyncio.create_task(
                        contender_attempt()
                    )

                    try:
                        # Observe the real PostgreSQL lock wait before
                        # committing the first writer. A mere asyncio
                        # overlap is not sufficient evidence.
                        blocked = False

                        for _ in range(100):
                            blocked = bool(
                                await observer_session.scalar(
                                    f3b3_sql("""
                                        SELECT
                                            CAST(:winner AS integer)
                                            = ANY(
                                                pg_blocking_pids(
                                                    CAST(:loser AS integer)
                                                )
                                            )
                                    """),
                                    {
                                        "winner": winner_pid,
                                        "loser": loser_pid,
                                    },
                                )
                            )
                            if blocked:
                                break

                            if contender_task.done():
                                break

                            await f3b3_asyncio.sleep(0.1)

                        assert blocked, (
                            "STOP: No real PostgreSQL lock contention "
                            "observed between independent writers"
                        )

                        print(
                            "F3-B3-B REAL PG LOCK CONTENTION = PASS",
                            flush=True,
                        )

                        # Publish all header, line and link records
                        # together, releasing the first writer's lock.
                        await winner_session.commit()

                        print(
                            "F3-B3-B WINNER COMMIT = PASS",
                            flush=True,
                        )

                        loser_outcome = await (
                            f3b3_asyncio.wait_for(
                                contender_task,
                                timeout=15,
                            )
                        )

                        assert loser_outcome[0] == "rejected", (
                            "STOP: Second writer was not rejected"
                        )
                        assert "Existing statutory result" in (
                            loser_outcome[1]
                        ), (
                            "STOP: Wrong second-writer refusal reason"
                        )

                        print(
                            "F3-B3-B LOSER CONTROLLED REJECTION = PASS",
                            flush=True,
                        )

                    finally:
                        if not contender_task.done():
                            await winner_session.rollback()
                            contender_task.cancel()
                            await f3b3_asyncio.gather(
                                contender_task,
                                return_exceptions=True,
                            )

                    # Use a fresh independent read after winner commit.
                    async with F3B3Session(
                        employee_engine,
                        expire_on_commit=False,
                    ) as verification_session:
                        await f3b3_context_check(
                            verification_session
                        )

                        actual = (
                            await verification_session.execute(
                                f3b3_sql("""
                                    SELECT
                                        COUNT(DISTINCT h.id),
                                        COUNT(DISTINCT l.id),
                                        COUNT(DISTINCT s.id)
                                    FROM payroll_statutory_results h
                                    LEFT JOIN payroll_statutory_result_lines l
                                      ON l.company_id = h.company_id
                                     AND l.payroll_statutory_result_id = h.id
                                    LEFT JOIN payroll_statutory_line_sources s
                                      ON s.company_id = l.company_id
                                     AND s.statutory_line_id = l.id
                                    WHERE h.company_id = :company_id
                                      AND h.payroll_calculation_id =
                                          :calculation_id
                                """),
                                {
                                    "company_id": f3b3_company_id,
                                    "calculation_id": f3b3_calculation_id,
                                },
                            )
                        ).one()

                        assert tuple(actual) == (1, 4, 6), (
                            "STOP: Concurrent writers produced "
                            "duplicate or incomplete evidence"
                        )

                        print(
                            "F3-B3-B EXACT ONE HEADER FOUR LINES "
                            "SIX LINKS = PASS",
                            flush=True,
                        )

        print(
            "F3-B3-B REAL CONCURRENT WRITER PG E2E = PASS",
            flush=True,
        )

        # G2-B2-A DATED FINANCIAL RECONCILIATION
        from decimal import Decimal as G2Decimal
        from sqlalchemy import text as g2_text
        from sqlalchemy.ext.asyncio import AsyncSession as G2Session

        async with G2Session(
            employee_engine,
            expire_on_commit=False,
        ) as g2_db:
            context = (
                await g2_db.execute(
                    g2_text("""
                        SELECT current_database(), current_schema()
                    """)
                )
            ).one()

            assert "_copy_" in context[0]
            assert context[1].startswith("test_")

            header = (
                await g2_db.execute(
                    g2_text("""
                        SELECT
                            id,
                            gross_amount,
                            employee_withholding_amount,
                            employer_contribution_amount,
                            net_amount
                        FROM payroll_statutory_results
                        WHERE company_id = :company_id
                          AND payroll_calculation_id = :calculation_id
                    """),
                    {
                        "company_id": f3b3_company_id,
                        "calculation_id": f3b3_calculation_id,
                    },
                )
            ).one()

            lines_data = (
                await g2_db.execute(
                    g2_text("""
                        SELECT
                            l.id,
                            l.line_no,
                            l.component,
                            l.source_rate_id,
                            l.base_amount,
                            l.amount,
                            r.rate,
                            count(s.id) AS source_count
                        FROM payroll_statutory_result_lines l
                        JOIN payroll_statutory_rates r
                          ON r.company_id = l.company_id
                         AND r.id = l.source_rate_id
                        LEFT JOIN payroll_statutory_line_sources s
                          ON s.company_id = l.company_id
                         AND s.statutory_line_id = l.id
                        WHERE l.company_id = :company_id
                          AND l.payroll_statutory_result_id = :header_id
                        GROUP BY
                            l.id,
                            l.line_no,
                            l.component,
                            l.source_rate_id,
                            l.base_amount,
                            l.amount,
                            r.rate
                        ORDER BY l.line_no
                    """),
                    {
                        "company_id": f3b3_company_id,
                        "header_id": header.id,
                    },
                )
            ).all()

            assert len(lines_data) == 4, (
                "STOP: Expected four dated statutory lines"
            )
            assert [r.line_no for r in lines_data] == [1, 2, 3, 4]
            assert sum(r.source_count for r in lines_data) == 6

            by_component = {}
            for row in lines_data:
                by_component.setdefault(str(row.component), []).append(row)

                amount = G2Decimal(row.amount)
                base = G2Decimal(row.base_amount)
                rate = G2Decimal(row.rate)

                assert amount >= 0
                assert base >= 0
                assert rate >= 0
                assert amount == amount.quantize(G2Decimal("0.01"))
                assert base == base.quantize(G2Decimal("0.01"))
                assert row.source_count >= 1

            assert sorted(map(len, by_component.values())) == [1, 1, 2], (
                "STOP: Expected two dated PIT lines and "
                "one line for each other statutory component"
            )

            gross = G2Decimal(header.gross_amount)
            withholding = G2Decimal(
                header.employee_withholding_amount
            )
            contribution = G2Decimal(
                header.employer_contribution_amount
            )
            net = G2Decimal(header.net_amount)

            assert gross >= 0 and net >= 0
            assert withholding >= 0 and contribution >= 0
            assert net == gross - withholding

            # Two lines of the same component must use different
            # statutory source-rate records.
            split_component = next(
                rows for rows in by_component.values()
                if len(rows) == 2
            )
            assert len({
                r.source_rate_id for r in split_component
            }) == 2

            # Financial reconciliation must preserve separate
            # withholding and employer contribution categories.
            from app.models.payroll_statutory import (
                PayrollStatutoryComponent as G2Component,
            )

            def component_value(value):
                return getattr(value, "value", value)

            totals = {}
            for row in lines_data:
                key = component_value(row.component)
                totals[key] = totals.get(
                    key, G2Decimal("0")
                ) + G2Decimal(row.amount)

            pit = totals.get(
                component_value(G2Component.PERSONAL_INCOME_TAX)
            )
            assert pit is not None
            assert pit == sum(
                G2Decimal(row.amount) for row in split_component
            )

            assert sum(totals.values()) == (
                withholding + contribution
            ), (
                "STOP: Statutory component totals do not "
                "reconcile to header liabilities"
            )

            print(
                "G2-B2-A DATED COMPONENT SPLIT = PASS",
                flush=True,
            )
            print(
                "G2-B2-A HEADER FINANCIAL RECONCILIATION = PASS",
                flush=True,
            )
            print(
                "G2-B2-A SIX SOURCE LINKS = PASS",
                flush=True,
            )
            print(
                "G2-B2-A POST-COMMIT INDEPENDENT READ = PASS",
                flush=True,
            )

        print(
            "G2-B2-A REAL DATED FINANCIAL PG E2E = PASS",
            flush=True,
        )

        # H1-B2-B2-B NEGATIVE INSERT POSTGRESQL E2E
        from sqlalchemy import text as h1_sql
        from sqlalchemy.ext.asyncio import AsyncSession as H1Session
        from sqlalchemy.exc import DBAPIError as H1DBAPIError
        from pathlib import Path as H1Path
        import ast as h1_ast

        h1_migration = H1Path(
            "alembic/versions/"
            "13d4a7b8c046_dated_statutory_seals.py"
        )

        h1_tree = h1_ast.parse(
            h1_migration.read_text(encoding="utf-8")
        )

        h1_upgrade = next(
            n for n in h1_tree.body
            if isinstance(n, h1_ast.FunctionDef)
            and n.name == "upgrade"
        )

        h1_statements = []

        for h1_node in h1_ast.walk(h1_upgrade):
            if (
                isinstance(h1_node, h1_ast.Call)
                and isinstance(h1_node.func, h1_ast.Attribute)
                and h1_node.func.attr == "execute"
                and len(h1_node.args) == 1
                and isinstance(h1_node.args[0], h1_ast.Constant)
                and isinstance(h1_node.args[0].value, str)
            ):
                h1_statements.append(h1_node.args[0].value)

        h1_objects = [
            statement for statement in h1_statements
            if (
                "CREATE FUNCTION erp_guard_dated_statutory_late_insert"
                in statement
                or "CREATE FUNCTION erp_guard_dated_statutory_seal_immutable"
                in statement
                or "CREATE TRIGGER trg_dated_statutory_line_no_late_insert"
                in statement
                or "CREATE TRIGGER trg_dated_statutory_source_no_late_insert"
                in statement
                or "CREATE TRIGGER trg_dated_statutory_seal_immutable"
                in statement
            )
        ]

        assert len(h1_objects) == 5, (
            "STOP: c046 guard contract changed"
        )

        async with H1Session(
            employee_engine,
            expire_on_commit=False,
        ) as h1_db:
            h1_database, h1_schema = (
                await h1_db.execute(
                    h1_sql(
                        "SELECT current_database(), current_schema()"
                    )
                )
            ).one()

            assert "_copy_" in h1_database
            assert h1_schema.startswith("test_")

            # Install the real migration's trigger/function definitions
            # in the isolated fixture schema when not already installed.
            # Never replace or bypass existing guards.
            h1_existing = set((
                await h1_db.execute(
                    h1_sql("""
                        SELECT tgname
                        FROM pg_trigger
                        WHERE tgrelid IN (
                            'payroll_statutory_result_lines'::regclass,
                            'payroll_statutory_line_sources'::regclass,
                            'payroll_dated_statutory_seals'::regclass
                        )
                        AND NOT tgisinternal
                    """)
                )
            ).scalars().all())

            h1_required_triggers = {
                "trg_dated_statutory_line_no_late_insert",
                "trg_dated_statutory_source_no_late_insert",
                "trg_dated_statutory_seal_immutable",
            }

            if h1_required_triggers.isdisjoint(h1_existing):
                for h1_statement in h1_objects:
                    await h1_db.execute(h1_sql(h1_statement))
            else:
                assert h1_required_triggers <= h1_existing, (
                    "STOP: Partial or inconsistent late-INSERT triggers"
                )

            h1_installed = set((
                await h1_db.execute(
                    h1_sql("""
                        SELECT tgname
                        FROM pg_trigger
                        WHERE tgrelid IN (
                            'payroll_statutory_result_lines'::regclass,
                            'payroll_statutory_line_sources'::regclass,
                            'payroll_dated_statutory_seals'::regclass
                        )
                        AND NOT tgisinternal
                    """)
                )
            ).scalars().all())

            assert h1_required_triggers <= h1_installed

            print(
                "H1-B2-B2-B TEST SCHEMA DB TRIGGERS = PASS",
                flush=True,
            )

            h1_header = (
                await h1_db.execute(
                    h1_sql("""
                        SELECT h.id
                        FROM payroll_statutory_results h
                        JOIN payroll_dated_statutory_seals s
                          ON s.company_id = h.company_id
                         AND s.payroll_statutory_result_id = h.id
                        WHERE h.company_id = :company_id
                          AND h.payroll_calculation_id = :calc_id
                    """),
                    {
                        "company_id": f3b3_company_id,
                        "calc_id": f3b3_calculation_id,
                    },
                )
            ).scalar_one()

            h1_before = tuple((
                await h1_db.execute(
                    h1_sql("""
                        SELECT
                            (SELECT count(*)
                             FROM payroll_statutory_result_lines
                             WHERE company_id = :company_id
                               AND payroll_statutory_result_id = :header_id),
                            (SELECT count(*)
                             FROM payroll_statutory_line_sources s
                             JOIN payroll_statutory_result_lines l
                               ON l.company_id = s.company_id
                              AND l.id = s.statutory_line_id
                             WHERE l.company_id = :company_id
                               AND l.payroll_statutory_result_id = :header_id),
                            (SELECT count(*)
                             FROM payroll_dated_statutory_seals
                             WHERE company_id = :company_id
                               AND payroll_statutory_result_id = :header_id)
                    """),
                    {
                        "company_id": f3b3_company_id,
                        "header_id": h1_header,
                    },
                )
            ).one())

            assert h1_before == (4, 6, 1), h1_before

            print(
                "H1-B2-B2-B SEALED 1 HEADER 4 LINES 6 LINKS = PASS",
                flush=True,
            )

            async def h1_expect_guard(sql, params, label):
                h1_savepoint = await h1_db.begin_nested()
                try:
                    try:
                        await h1_db.execute(h1_sql(sql), params)
                    except H1DBAPIError as exc:
                        original_error = exc.orig
                        state = (
                            getattr(original_error, "sqlstate", None)
                            or getattr(original_error, "pgcode", None)
                            or getattr(
                                getattr(original_error, "__cause__", None),
                                "sqlstate",
                                None,
                            )
                        )
                        assert state == "23514", (
                            f"STOP: {label} rejected by unexpected "
                            f"SQLSTATE {state}: {exc}"
                        )
                    else:
                        raise AssertionError(
                            "STOP: Illegal mutation was accepted: " + label
                        )
                finally:
                    if h1_savepoint.is_active:
                        await h1_savepoint.rollback()

                print(label + " = PASS", flush=True)

            # Dynamically reuse the actual table columns so the attempted
            # INSERT reaches the database trigger rather than failing
            # prematurely because of a missing required column.
            async def h1_clone_sql(table, source_where, overrides):
                columns = (
                    await h1_db.execute(
                        h1_sql("""
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = current_schema()
                              AND table_name = :table
                            ORDER BY ordinal_position
                        """),
                        {"table": table},
                    )
                ).scalars().all()

                columns = [c for c in columns if c != "id"]
                assert columns

                selected = [
                    overrides.get(c, '"' + c + '"')
                    for c in columns
                ]

                return (
                    f'INSERT INTO "{table}" ('
                    + ", ".join('"' + c + '"' for c in columns)
                    + ") SELECT "
                    + ", ".join(selected)
                    + f' FROM "{table}" WHERE {source_where}'
                    + " LIMIT 1"
                )

            h1_line_sql = await h1_clone_sql(
                "payroll_statutory_result_lines",
                "company_id = :company_id "
                "AND payroll_statutory_result_id = :header_id",
                {"line_no": "line_no + 1000"},
            )

            await h1_expect_guard(
                h1_line_sql,
                {
                    "company_id": f3b3_company_id,
                    "header_id": h1_header,
                },
                "H1-B2-B2-B LATE STATUTORY LINE INSERT REJECTED",
            )

            h1_link_sql = await h1_clone_sql(
                "payroll_statutory_line_sources",
                "company_id = :company_id "
                "AND statutory_line_id IN ("
                "SELECT id FROM payroll_statutory_result_lines "
                "WHERE company_id = :company_id "
                "AND payroll_statutory_result_id = :header_id)",
                {},
            )

            await h1_expect_guard(
                h1_link_sql,
                {
                    "company_id": f3b3_company_id,
                    "header_id": h1_header,
                },
                "H1-B2-B2-B LATE PROVENANCE LINK INSERT REJECTED",
            )

            await h1_expect_guard(
                """
                UPDATE payroll_dated_statutory_seals
                SET sealed_at = sealed_at
                WHERE company_id = :company_id
                  AND payroll_statutory_result_id = :header_id
                """,
                {
                    "company_id": f3b3_company_id,
                    "header_id": h1_header,
                },
                "H1-B2-B2-B SEAL UPDATE REJECTED",
            )

            await h1_expect_guard(
                """
                DELETE FROM payroll_dated_statutory_seals
                WHERE company_id = :company_id
                  AND payroll_statutory_result_id = :header_id
                """,
                {
                    "company_id": f3b3_company_id,
                    "header_id": h1_header,
                },
                "H1-B2-B2-B SEAL DELETE REJECTED",
            )

            h1_after = tuple((
                await h1_db.execute(
                    h1_sql("""
                        SELECT
                            (SELECT count(*)
                             FROM payroll_statutory_result_lines
                             WHERE company_id = :company_id
                               AND payroll_statutory_result_id = :header_id),
                            (SELECT count(*)
                             FROM payroll_statutory_line_sources s
                             JOIN payroll_statutory_result_lines l
                               ON l.company_id = s.company_id
                              AND l.id = s.statutory_line_id
                             WHERE l.company_id = :company_id
                               AND l.payroll_statutory_result_id = :header_id),
                            (SELECT count(*)
                             FROM payroll_dated_statutory_seals
                             WHERE company_id = :company_id
                               AND payroll_statutory_result_id = :header_id)
                    """),
                    {
                        "company_id": f3b3_company_id,
                        "header_id": h1_header,
                    },
                )
            ).one())

            assert h1_after == h1_before == (4, 6, 1)

            print(
                "H1-B2-B2-B NEGATIVE MUTATIONS ZERO LEAK = PASS",
                flush=True,
            )

            await h1_db.rollback()

        print(
            "H1-B2-B2-B REAL POSTGRESQL INSERT IMMUTABILITY = PASS",
            flush=True,
        )

        # G2-B2-B2-A REAL PAYSLIP CONSUMER E2E
        from sqlalchemy.ext.asyncio import AsyncSession as G2PayslipSession
        from sqlalchemy import text as g2_payslip_text
        from app.services.payroll_payslip_service import (
            get_payslip_generation_sources,
            generate_payroll_payslip,
            get_payroll_payslip_by_calculation,
            list_payroll_payslip_lines,
        )

        async with G2PayslipSession(
            employee_engine,
            expire_on_commit=False,
        ) as payslip_db:

            payslip_context = (
                await payslip_db.execute(
                    g2_payslip_text("""
                        SELECT current_database(), current_schema()
                    """)
                )
            ).one()

            assert "_copy_" in payslip_context[0], (
                "STOP: Payslip test is not in isolated copy"
            )
            assert payslip_context[1].startswith("test_"), (
                "STOP: Payslip test schema mismatch"
            )

            existing_payslip = await get_payroll_payslip_by_calculation(
                payslip_db,
                company_id=f3b3_company_id,
                payroll_calculation_id=f3b3_calculation_id,
            )

            assert existing_payslip is None, (
                "STOP: Payslip already exists before integration test"
            )

            real_sources = await get_payslip_generation_sources(
                payslip_db,
                company_id=f3b3_company_id,
                payroll_calculation_id=f3b3_calculation_id,
            )

            assert len(real_sources) == 6

            (
                source_calculation,
                source_statutory,
                source_contract,
                source_employee,
                salary_lines,
                statutory_lines,
            ) = real_sources

            assert source_calculation.id == f3b3_calculation_id
            assert source_statutory.payroll_calculation_id == (
                f3b3_calculation_id
            )
            assert source_contract is not None
            assert source_employee is not None
            assert len(salary_lines) == 2
            assert len(statutory_lines) == 4

            assert sorted(line.line_no for line in statutory_lines) == [
                1, 2, 3, 4
            ]

            print(
                "G2-B2-B2-A REAL PAYSLIP SOURCE LOAD = PASS",
                flush=True,
            )

            # The savepoint ensures the generated payslip and its
            # lines do not persist beyond this integration proof.
            payslip_savepoint = await payslip_db.begin_nested()

            try:
                generated = await generate_payroll_payslip(
                    payslip_db,
                    company_id=f3b3_company_id,
                    payroll_calculation_id=f3b3_calculation_id,
                    actor_user_id=f3b3_actor_id,
                )

                assert generated.id is not None
                await payslip_db.flush()

                reloaded = await get_payroll_payslip_by_calculation(
                    payslip_db,
                    company_id=f3b3_company_id,
                    payroll_calculation_id=f3b3_calculation_id,
                )

                assert reloaded is not None
                assert reloaded.id == generated.id

                generated_lines = await list_payroll_payslip_lines(
                    payslip_db,
                    company_id=f3b3_company_id,
                    payroll_payslip_id=generated.id,
                )

                assert generated_lines, (
                    "STOP: Payslip generated without lines"
                )

                print(
                    "G2-B2-B2-A REAL PAYSLIP GENERATED = PASS",
                    flush=True,
                )
                print(
                    "G2-B2-B2-A PAYSLIP RELOAD = PASS",
                    flush=True,
                )
            finally:
                if payslip_savepoint.is_active:
                    await payslip_savepoint.rollback()

            after_rollback = await get_payroll_payslip_by_calculation(
                payslip_db,
                company_id=f3b3_company_id,
                payroll_calculation_id=f3b3_calculation_id,
            )

            assert after_rollback is None, (
                "STOP: Payslip savepoint rollback leaked records"
            )

            print(
                "G2-B2-B2-A PAYSLIP ROLLBACK ZERO LEAK = PASS",
                flush=True,
            )

        print(
            "G2-B2-B2-A REAL WRITER TO PAYSLIP PG E2E = PASS",
            flush=True,
        )

        # G2-B2-B2-B3 REAL ACCOUNTING JOURNAL E2E
        from decimal import Decimal as G2AccountingDecimal
        from sqlalchemy import select as g2_accounting_select
        from sqlalchemy import text as g2_accounting_text
        from sqlalchemy.ext.asyncio import AsyncSession as G2AccountingSession
        from app.models.account import (
            Account as G2Account,
            AccountType as G2AccountType,
            AccountNormalBalance as G2NormalBalance,
        )
        from app.models.accounting_period import AccountingPeriod as G2AccountingPeriod
        from app.models.journal_entry_line import JournalEntryLine as G2JournalLine
        from app.models.journal_entry import JournalEntry as G2Journal
        from app.services.payroll_accounting_service import (
            generate_and_post_payroll_journal_entry,
            get_payroll_accounting_journal,
        )

        async with G2AccountingSession(
            employee_engine,
            expire_on_commit=False,
        ) as accounting_db:
            database_name, schema_name = (
                await accounting_db.execute(
                    g2_accounting_text(
                        "SELECT current_database(), current_schema()"
                    )
                )
            ).one()

            assert "_copy_" in database_name
            assert schema_name.startswith("test_")

            existing_journal = await get_payroll_accounting_journal(
                accounting_db,
                company_id=f3b3_company_id,
                payroll_calculation_id=f3b3_calculation_id,
            )
            assert existing_journal is None

            # Every setup write and journal write belongs to the
            # same disposable savepoint.
            accounting_sp = await accounting_db.begin_nested()

            try:
                from datetime import date as G2Date

                active_period = await accounting_db.scalar(
                    g2_accounting_select(G2AccountingPeriod).where(
                        G2AccountingPeriod.company_id == f3b3_company_id,
                        G2AccountingPeriod.year == 2026,
                        G2AccountingPeriod.month == 9,
                    )
                )

                if active_period is None:
                    accounting_db.add(
                        G2AccountingPeriod(
                            company_id=f3b3_company_id,
                            year=2026,
                            month=9,
                            start_date=G2Date(2026, 9, 1),
                            end_date=G2Date(2026, 9, 30),
                            status="open",
                            is_locked=False,
                        )
                    )
                else:
                    assert active_period.status == "open"
                    assert not active_period.is_locked

                # Reuse the real account-code configuration from
                # test_payroll_accounting_postgresql.py.
                account_specs = (
                    (
                        "92", "Payroll expense",
                        G2AccountType.EXPENSE,
                        G2NormalBalance.DEBIT,
                    ),
                    (
                        "66", "Payroll payable",
                        G2AccountType.LIABILITY,
                        G2NormalBalance.CREDIT,
                    ),
                    (
                        "64", "Tax payable",
                        G2AccountType.LIABILITY,
                        G2NormalBalance.CREDIT,
                    ),
                    (
                        "65", "Social contribution payable",
                        G2AccountType.LIABILITY,
                        G2NormalBalance.CREDIT,
                    ),
                )

                for code, name, account_type, normal_balance in account_specs:
                    account = await accounting_db.scalar(
                        g2_accounting_select(G2Account).where(
                            G2Account.company_id == f3b3_company_id,
                            G2Account.code == code,
                        )
                    )

                    if account is None:
                        accounting_db.add(
                            G2Account(
                                company_id=f3b3_company_id,
                                code=code,
                                name=name,
                                account_type=account_type,
                                normal_balance=normal_balance,
                                is_postable=True,
                                is_system=True,
                                is_active=True,
                            )
                        )
                    else:
                        assert account.is_active
                        assert account.is_postable

                await accounting_db.flush()

                print(
                    "G2-B2-B2-B3 ACCOUNTING FIXTURE READY = PASS",
                    flush=True,
                )

                journal = await generate_and_post_payroll_journal_entry(
                    accounting_db,
                    company_id=f3b3_company_id,
                    payroll_calculation_id=f3b3_calculation_id,
                    created_by=f3b3_actor_id,
                )

                assert journal.id is not None
                assert journal.status == "posted"
                assert journal.payroll_calculation_id == (
                    f3b3_calculation_id
                )

                journal_id = int(journal.id)
                await accounting_db.flush()

                print(
                    "G2-B2-B2-B3 REAL JOURNAL POSTED = PASS",
                    flush=True,
                )

                posted_lines = list((
                    await accounting_db.scalars(
                        g2_accounting_select(G2JournalLine)
                        .where(
                            G2JournalLine.journal_entry_id == journal_id
                        )
                        .order_by(G2JournalLine.line_no)
                    )
                ).all())

                assert posted_lines, (
                    "STOP: Journal contains no lines"
                )

                debits = sum(
                    (
                        G2AccountingDecimal(row.debit)
                        for row in posted_lines
                    ),
                    G2AccountingDecimal("0.00"),
                )
                credits = sum(
                    (
                        G2AccountingDecimal(row.credit)
                        for row in posted_lines
                    ),
                    G2AccountingDecimal("0.00"),
                )

                assert debits > 0
                assert debits == credits

                header_financials = (
                    await accounting_db.execute(
                        g2_accounting_text("""
                            SELECT gross_amount,
                                   employee_withholding_amount,
                                   employer_contribution_amount,
                                   net_amount,
                                   id
                            FROM payroll_statutory_results
                            WHERE company_id = :company_id
                              AND payroll_calculation_id = :calc_id
                        """),
                        {
                            "company_id": f3b3_company_id,
                            "calc_id": f3b3_calculation_id,
                        },
                    )
                ).one()

                component_rows = (
                    await accounting_db.execute(
                        g2_accounting_text("""
                            SELECT component, SUM(amount)
                            FROM payroll_statutory_result_lines
                            WHERE company_id = :company_id
                              AND payroll_statutory_result_id = :header_id
                            GROUP BY component
                        """),
                        {
                            "company_id": f3b3_company_id,
                            "header_id": header_financials.id,
                        },
                    )
                ).all()

                component_totals = {
                    str(component): G2AccountingDecimal(amount)
                    for component, amount in component_rows
                }

                from app.models.payroll_statutory import (
                    PayrollStatutoryComponent as G2AccountingComponent
                )

                pit = component_totals[
                    G2AccountingComponent.PERSONAL_INCOME_TAX.value
                ]
                military = component_totals[
                    G2AccountingComponent.MILITARY_LEVY.value
                ]
                usc = component_totals[
                    G2AccountingComponent.UNIFIED_SOCIAL_CONTRIBUTION.value
                ]

                gross = G2AccountingDecimal(
                    header_financials.gross_amount
                )
                net = G2AccountingDecimal(
                    header_financials.net_amount
                )
                withholding = G2AccountingDecimal(
                    header_financials.employee_withholding_amount
                )
                employer = G2AccountingDecimal(
                    header_financials.employer_contribution_amount
                )

                assert pit + military == withholding
                assert usc == employer
                assert gross - withholding == net
                assert debits == gross + employer
                assert credits == net + pit + military + usc

                by_description = {
                    row.description: row
                    for row in posted_lines
                }

                expected_credits = {
                    "Payroll net payable": net,
                    "Personal income tax payable": pit,
                    "Military levy payable": military,
                    "Unified social contribution payable": usc,
                }

                for description, amount in expected_credits.items():
                    if amount == G2AccountingDecimal("0.00"):
                        continue
                    matching = [
                        line for line in posted_lines
                        if line.description == description
                    ]
                    assert len(matching) == 1, (
                        "STOP: Missing or duplicated accounting line: "
                        + description
                    )
                    assert G2AccountingDecimal(
                        matching[0].credit
                    ) == amount

                print(
                    "G2-B2-B2-B3 DATED TAX ACCOUNTING = PASS",
                    flush=True,
                )
                print(
                    "G2-B2-B2-B3 DEBIT CREDIT RECONCILIATION = PASS",
                    flush=True,
                )

                # G2-B2-B2-C2 REAL TAX BALANCE E2E
                from datetime import date as G2TaxDate
                from app.services.payroll_tax_balance_service import (
                    payroll_tax_balances as g2_tax_balances,
                )

                g2_as_of = G2TaxDate(2026, 9, 30)

                tax_response = await g2_tax_balances(
                    accounting_db,
                    company_id=f3b3_company_id,
                    as_of=g2_as_of,
                )

                assert tax_response["company_id"] == f3b3_company_id
                assert tax_response["currency_code"] == "UAH"

                dated_tax_rows = [
                    row for row in tax_response["rows"]
                    if row["payroll_calculation_id"] == f3b3_calculation_id
                ]

                assert len(dated_tax_rows) == 3, (
                    "STOP: Expected exactly three aggregated tax components"
                )

                from app.models.payroll_statutory import (
                    PayrollStatutoryComponent as G2TaxComponent,
                )

                expected_tax = {
                    G2TaxComponent.PERSONAL_INCOME_TAX.value: pit,
                    G2TaxComponent.MILITARY_LEVY.value: military,
                    G2TaxComponent.UNIFIED_SOCIAL_CONTRIBUTION.value: usc,
                }

                actual_components = {
                    row["component"]: row
                    for row in dated_tax_rows
                }

                assert set(actual_components) == set(expected_tax)

                for component_name, expected_amount in expected_tax.items():
                    row = actual_components[component_name]

                    assert G2AccountingDecimal(
                        row["accrued_amount"]
                    ) == expected_amount, (
                        "STOP: Tax accrual does not match dated source: "
                        + component_name
                    )

                    assert G2AccountingDecimal(
                        row["paid_amount"]
                    ) == G2AccountingDecimal("0.00")

                    assert G2AccountingDecimal(
                        row["outstanding_amount"]
                    ) == expected_amount

                    assert row["accrual_journal_id"] == journal_id

                assert sum(
                    expected_tax.values(),
                    G2AccountingDecimal("0.00"),
                ) == withholding + employer

                print(
                    "G2-B2-B2-C2 TWO-RATE PIT AGGREGATION = PASS",
                    flush=True,
                )

                print(
                    "G2-B2-B2-C2 THREE TAX LIABILITIES = PASS",
                    flush=True,
                )

                print(
                    "G2-B2-B2-C2 ZERO PAYMENTS / FULL OUTSTANDING = PASS",
                    flush=True,
                )

                print(
                    "G2-B2-B2-C2 REAL POSTED TAX BALANCE = PASS",
                    flush=True,
                )

                # G2-B2-B2-C3-B REAL BANK REMITTANCE E2E
                from app.models.bank_account import BankAccount as G2TaxBank
                from app.models.payroll import PayrollCalculation as G2TaxCalculation
                from app.services.payroll_tax_remittance_service import (
                    register_tax_remittance as g2_register_tax,
                    TaxRemittanceError as G2TaxRemittanceError,
                )
                from test_payroll_tax_remittance import (
                    payment as g2_create_bank_payment,
                    request as g2_make_tax_request,
                )

                g2_tax_bank_gl = await accounting_db.scalar(
                    g2_accounting_select(G2Account).where(
                        G2Account.company_id == f3b3_company_id,
                        G2Account.code == "311",
                    )
                )

                if g2_tax_bank_gl is None:
                    g2_tax_bank_gl = G2Account(
                        company_id=f3b3_company_id,
                        code="311",
                        name="Bank UAH dated payroll tax E2E",
                        account_type=G2AccountType.ASSET,
                        normal_balance=G2NormalBalance.DEBIT,
                        is_postable=True,
                        is_active=True,
                    )
                    accounting_db.add(g2_tax_bank_gl)
                    await accounting_db.flush()

                assert g2_tax_bank_gl.account_type == G2AccountType.ASSET
                assert g2_tax_bank_gl.is_active
                assert g2_tax_bank_gl.is_postable

                g2_bank = G2TaxBank(
                    company_id=f3b3_company_id,
                    name="Dated Payroll Tax E2E",
                    account_number="G2-DATED-TAX-E2E",
                    currency_code="UAH",
                    accounting_account_id=g2_tax_bank_gl.id,
                    is_active=True,
                )
                accounting_db.add(g2_bank)
                await accounting_db.flush()

                g2_calc = await accounting_db.scalar(
                    g2_accounting_select(G2TaxCalculation).where(
                        G2TaxCalculation.company_id == f3b3_company_id,
                        G2TaxCalculation.id == f3b3_calculation_id,
                    )
                )
                assert g2_calc is not None

                g2_pit_gl = await accounting_db.scalar(
                    g2_accounting_select(G2Account).where(
                        G2Account.company_id == f3b3_company_id,
                        G2Account.code == "64",
                    )
                )
                g2_usc_gl = await accounting_db.scalar(
                    g2_accounting_select(G2Account).where(
                        G2Account.company_id == f3b3_company_id,
                        G2Account.code == "65",
                    )
                )
                assert g2_pit_gl is not None
                assert g2_usc_gl is not None

                g2_payment_accounts = {
                    "personal_income_tax": g2_pit_gl.id,
                    "military_levy": g2_pit_gl.id,
                    "unified_social_contribution": g2_usc_gl.id,
                    "64": g2_pit_gl.id,
                    "65": g2_usc_gl.id,
                }

                assert pit > G2AccountingDecimal("0.02")

                g2_payment_amount = (
                    pit / G2AccountingDecimal("2")
                ).quantize(G2AccountingDecimal("0.01"))

                g2_company_ref = type(
                    "G2CompanyRef", (), {"id": f3b3_company_id}
                )()
                g2_user_ref = type(
                    "G2UserRef", (), {"id": f3b3_actor_id}
                )()

                g2_payment = await g2_create_bank_payment(
                    accounting_db,
                    g2_company_ref,
                    g2_user_ref,
                    g2_payment_accounts,
                    g2_bank,
                    g2_payment_amount,
                    component="personal_income_tax",
                    day=g2_as_of,
                )

                assert g2_payment.id is not None
                assert g2_payment.status == "posted"

                print(
                    "G2-B2-B2-C3-B REAL BANK JOURNAL POSTED = PASS",
                    flush=True,
                )

                g2_request = g2_make_tax_request(
                    g2_payment,
                    g2_bank,
                    g2_calc,
                    g2_payment_amount,
                    "g2-dated-pit-partial-v1",
                    "personal_income_tax",
                )

                g2_registered = await g2_register_tax(
                    accounting_db,
                    company_id=f3b3_company_id,
                    data=g2_request,
                    created_by=f3b3_actor_id,
                )

                assert g2_registered.id is not None
                assert G2AccountingDecimal(
                    g2_registered.amount
                ) == g2_payment_amount

                g2_remittance_id = int(g2_registered.id)

                print(
                    "G2-B2-B2-C3-B PARTIAL PIT PAYMENT REGISTERED = PASS",
                    flush=True,
                )

                g2_after_payment = await g2_tax_balances(
                    accounting_db,
                    company_id=f3b3_company_id,
                    as_of=g2_as_of,
                )

                g2_after_rows = {
                    row["component"]: row
                    for row in g2_after_payment["rows"]
                    if row["payroll_calculation_id"] == f3b3_calculation_id
                }

                assert len(g2_after_rows) == 3

                g2_pit_balance = g2_after_rows["personal_income_tax"]

                assert G2AccountingDecimal(
                    g2_pit_balance["accrued_amount"]
                ) == pit
                assert G2AccountingDecimal(
                    g2_pit_balance["paid_amount"]
                ) == g2_payment_amount
                assert G2AccountingDecimal(
                    g2_pit_balance["outstanding_amount"]
                ) == pit - g2_payment_amount

                for g2_other in ("military_levy", "unified_social_contribution"):
                    g2_row = g2_after_rows[g2_other]
                    assert G2AccountingDecimal(
                        g2_row["paid_amount"]
                    ) == G2AccountingDecimal("0.00")
                    assert G2AccountingDecimal(
                        g2_row["outstanding_amount"]
                    ) == expected_tax[g2_other]

                print(
                    "G2-B2-B2-C3-B PIT OUTSTANDING RECONCILIATION = PASS",
                    flush=True,
                )

                g2_repeated = await g2_register_tax(
                    accounting_db,
                    company_id=f3b3_company_id,
                    data=g2_request,
                    created_by=f3b3_actor_id,
                )
                assert g2_repeated.id == g2_remittance_id

                print(
                    "G2-B2-B2-C3-B REQUEST IDEMPOTENCE = PASS",
                    flush=True,
                )

                g2_duplicate_request = g2_request.model_copy(
                    update={"request_key": "g2-dated-pit-duplicate-v1"}
                )

                g2_duplicate_savepoint = await accounting_db.begin_nested()
                try:
                    try:
                        await g2_register_tax(
                            accounting_db,
                            company_id=f3b3_company_id,
                            data=g2_duplicate_request,
                            created_by=f3b3_actor_id,
                        )
                    except G2TaxRemittanceError as exc:
                        assert "already attributed" in str(exc).lower()
                    else:
                        raise AssertionError(
                            "STOP: Same bank journal was attributed twice"
                        )
                finally:
                    if g2_duplicate_savepoint.is_active:
                        await g2_duplicate_savepoint.rollback()

                print(
                    "G2-B2-B2-C3-B DUPLICATE BANK ATTRIBUTION REJECTED = PASS",
                    flush=True,
                )

                g2_after_rejection = await g2_tax_balances(
                    accounting_db,
                    company_id=f3b3_company_id,
                    as_of=g2_as_of,
                )

                g2_final_pit = next(
                    row for row in g2_after_rejection["rows"]
                    if row["payroll_calculation_id"] == f3b3_calculation_id
                    and row["component"] == "personal_income_tax"
                )

                assert G2AccountingDecimal(
                    g2_final_pit["paid_amount"]
                ) == g2_payment_amount

                print(
                    "G2-B2-B2-C3-B REJECTED PAYMENT ZERO LEAK = PASS",
                    flush=True,
                )

                # G2-D2 REAL DATED SALARY DISBURSEMENT E2E
                from app.services.payroll_disbursement_service import (
                    create_payroll_disbursement as g2_create_salary_payment,
                    generate_and_post_payroll_disbursement_journal
                        as g2_post_salary_payment,
                    get_payroll_disbursement_journal
                        as g2_get_salary_payment_journal,
                    PayrollDisbursementSourceStateError
                        as G2SalaryPaymentStateError,
                )
                from app.models.payroll_disbursement import (
                    PayrollDisbursement as G2SalaryDisbursement,
                )

                g2_salary_net = G2AccountingDecimal(net)

                assert g2_salary_net > G2AccountingDecimal("0.02")

                g2_salary_partial = (
                    g2_salary_net / G2AccountingDecimal("2")
                ).quantize(G2AccountingDecimal("0.01"))

                assert G2AccountingDecimal("0") < g2_salary_partial < g2_salary_net

                g2_salary_payment_date = g2_as_of
                g2_salary_request_key = "g2-dated-salary-partial-v1"

                # G2-D2-R2 EMPLOYEE PAYMENT IBAN FIXTURE
                from app.models.employee import Employee as G2PaymentEmployee
                from app.models.employment_contract import (
                    EmploymentContract as G2PaymentContract,
                )

                g2_payment_contract = await accounting_db.scalar(
                    g2_accounting_select(G2PaymentContract).where(
                        G2PaymentContract.company_id == f3b3_company_id,
                        G2PaymentContract.id == g2_calc.employment_contract_id,
                    )
                )
                assert g2_payment_contract is not None

                g2_payment_employee = await accounting_db.scalar(
                    g2_accounting_select(G2PaymentEmployee).where(
                        G2PaymentEmployee.id == g2_payment_contract.employee_id,
                    )
                )
                assert g2_payment_employee is not None

                g2_payment_employee.payment_iban = (
                    "UA123456789012345678901234567"
                )
                await accounting_db.flush()

                assert g2_payment_employee.payment_iban

                print(
                    "G2-D2-R2 EMPLOYEE PAYMENT IBAN = PASS",
                    flush=True,
                )

                g2_salary_payment = await g2_create_salary_payment(
                    accounting_db,
                    company_id=f3b3_company_id,
                    payroll_calculation_id=f3b3_calculation_id,
                    bank_account_id=g2_bank.id,
                    payment_date=g2_salary_payment_date,
                    created_by=f3b3_actor_id,
                    amount=g2_salary_partial,
                    request_key=g2_salary_request_key,
                )

                assert g2_salary_payment.id is not None
                assert G2AccountingDecimal(
                    g2_salary_payment.amount
                ) == g2_salary_partial

                g2_salary_payment_id = int(g2_salary_payment.id)

                print(
                    "G2-D2 REAL PARTIAL SALARY DISBURSEMENT = PASS",
                    flush=True,
                )

                g2_salary_repeated = await g2_create_salary_payment(
                    accounting_db,
                    company_id=f3b3_company_id,
                    payroll_calculation_id=f3b3_calculation_id,
                    bank_account_id=g2_bank.id,
                    payment_date=g2_salary_payment_date,
                    created_by=f3b3_actor_id,
                    amount=g2_salary_partial,
                    request_key=g2_salary_request_key,
                )

                assert int(g2_salary_repeated.id) == g2_salary_payment_id

                print(
                    "G2-D2 SALARY REQUEST IDEMPOTENCE = PASS",
                    flush=True,
                )

                g2_salary_journal = await g2_post_salary_payment(
                    accounting_db,
                    company_id=f3b3_company_id,
                    payroll_disbursement_id=g2_salary_payment_id,
                    created_by=f3b3_actor_id,
                )

                assert g2_salary_journal.id is not None
                assert g2_salary_journal.status == "posted"

                g2_salary_journal_id = int(g2_salary_journal.id)
                await accounting_db.flush()

                g2_salary_journal_lines = list((
                    await accounting_db.scalars(
                        g2_accounting_select(G2JournalLine).where(
                            G2JournalLine.journal_entry_id
                            == g2_salary_journal_id
                        )
                    )
                ).all())

                assert g2_salary_journal_lines

                g2_salary_debits = sum(
                    (
                        G2AccountingDecimal(line.debit)
                        for line in g2_salary_journal_lines
                    ),
                    G2AccountingDecimal("0.00"),
                )

                g2_salary_credits = sum(
                    (
                        G2AccountingDecimal(line.credit)
                        for line in g2_salary_journal_lines
                    ),
                    G2AccountingDecimal("0.00"),
                )

                assert g2_salary_debits == g2_salary_partial
                assert g2_salary_credits == g2_salary_partial

                print(
                    "G2-D2 POSTED SALARY BANK JOURNAL = PASS",
                    flush=True,
                )
                print(
                    "G2-D2 NET SALARY DEBIT CREDIT = PASS",
                    flush=True,
                )

                g2_loaded_salary_journal = (
                    await g2_get_salary_payment_journal(
                        accounting_db,
                        company_id=f3b3_company_id,
                        payroll_disbursement_id=g2_salary_payment_id,
                    )
                )

                assert g2_loaded_salary_journal is not None
                assert g2_loaded_salary_journal.id == g2_salary_journal_id

                g2_reposted_salary = await g2_post_salary_payment(
                    accounting_db,
                    company_id=f3b3_company_id,
                    payroll_disbursement_id=g2_salary_payment_id,
                    created_by=f3b3_actor_id,
                )

                assert g2_reposted_salary.id == g2_salary_journal_id

                print(
                    "G2-D2 SALARY POSTING IDEMPOTENCE = PASS",
                    flush=True,
                )

                # Request more than the unpaid net remainder:
                # this must be rejected without a second payment record.
                g2_before_rejection = await accounting_db.scalar(
                    g2_accounting_select(
                        __import__("sqlalchemy").func.count(
                            G2SalaryDisbursement.id
                        )
                    ).where(
                        G2SalaryDisbursement.company_id == f3b3_company_id,
                        G2SalaryDisbursement.payroll_calculation_id
                        == f3b3_calculation_id,
                    )
                )

                g2_overpay_savepoint = await accounting_db.begin_nested()

                try:
                    try:
                        await g2_create_salary_payment(
                            accounting_db,
                            company_id=f3b3_company_id,
                            payroll_calculation_id=f3b3_calculation_id,
                            bank_account_id=g2_bank.id,
                            payment_date=g2_salary_payment_date,
                            created_by=f3b3_actor_id,
                            amount=g2_salary_net,
                            request_key="g2-dated-salary-overpay-v1",
                        )
                    except G2SalaryPaymentStateError as exc:
                        assert (
                            "remaining" in str(exc).lower()
                            or "exceed" in str(exc).lower()
                        ), str(exc)
                    else:
                        raise AssertionError(
                            "STOP: Salary overpayment was accepted"
                        )
                finally:
                    if g2_overpay_savepoint.is_active:
                        await g2_overpay_savepoint.rollback()

                g2_after_rejection = await accounting_db.scalar(
                    g2_accounting_select(
                        __import__("sqlalchemy").func.count(
                            G2SalaryDisbursement.id
                        )
                    ).where(
                        G2SalaryDisbursement.company_id == f3b3_company_id,
                        G2SalaryDisbursement.payroll_calculation_id
                        == f3b3_calculation_id,
                    )
                )

                assert g2_after_rejection == g2_before_rejection == 1

                print(
                    "G2-D2 SALARY OVERPAYMENT REJECTED = PASS",
                    flush=True,
                )
                print(
                    "G2-D2 REJECTED SALARY WRITE ZERO LEAK = PASS",
                    flush=True,
                )

                loaded = await get_payroll_accounting_journal(
                    accounting_db,
                    company_id=f3b3_company_id,
                    payroll_calculation_id=f3b3_calculation_id,
                )

                assert loaded is not None
                assert loaded.id == journal_id

                repeated = await generate_and_post_payroll_journal_entry(
                    accounting_db,
                    company_id=f3b3_company_id,
                    payroll_calculation_id=f3b3_calculation_id,
                    created_by=f3b3_actor_id,
                )

                assert repeated.id == journal_id

                journal_count = await accounting_db.scalar(
                    g2_accounting_select(
                        __import__(
                            "sqlalchemy"
                        ).func.count(G2Journal.id)
                    ).where(
                        G2Journal.company_id == f3b3_company_id,
                        G2Journal.payroll_calculation_id == (
                            f3b3_calculation_id
                        ),
                    )
                )
                assert journal_count == 1

                print(
                    "G2-B2-B2-B3 JOURNAL RELOAD IDEMPOTENCE = PASS",
                    flush=True,
                )

            finally:
                if accounting_sp.is_active:
                    await accounting_sp.rollback()

            leaked = await get_payroll_accounting_journal(
                accounting_db,
                company_id=f3b3_company_id,
                payroll_calculation_id=f3b3_calculation_id,
            )

            assert leaked is None, (
                "STOP: Accounting savepoint rollback leaked journal"
            )

            print(
                "G2-B2-B2-B3 ACCOUNTING ROLLBACK ZERO LEAK = PASS",
                flush=True,
            )

            # G2-B2-B2-C2 POST-ROLLBACK TAX BALANCE
            after_tax_rollback = await g2_tax_balances(
                accounting_db,
                company_id=f3b3_company_id,
                as_of=g2_as_of,
            )

            assert not [
                row for row in after_tax_rollback["rows"]
                if row["payroll_calculation_id"] == f3b3_calculation_id
            ], (
                "STOP: Rolled-back accrual still recognized as a tax liability"
            )

            print(
                "G2-B2-B2-C2 ROLLBACK REMOVES TAX ACCRUAL = PASS",
                flush=True,
            )

        print(
            "G2-B2-B2-B3 REAL WRITER TO ACCOUNTING PG E2E = PASS",
            flush=True,
        )
