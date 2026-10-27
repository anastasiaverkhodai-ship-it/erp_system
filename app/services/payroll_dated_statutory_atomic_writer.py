"""Atomic persistence of verified dated salary statutory results.

Internal entry point only. Not connected to payroll API or legacy writer.
Caller controls the outer transaction; this function never commits it.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
)
from app.models.payroll_statutory import (
    PayrollStatutoryRate,
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
    PayrollStatutoryLineSource,
)
from app.services.payroll_dated_statutory_verified_plan import (
    build_verified_dated_salary_statutory_plan,
)


class DatedStatutoryAtomicWriterError(ValueError):
    pass


COMPONENTS = {
    "personal_income_tax",
    "military_levy",
    "unified_social_contribution",
}


def _require_id(value, label):
    if type(value) is not int or value <= 0:
        raise DatedStatutoryAtomicWriterError(
            f"Invalid {label} identifier"
        )


def _require_money(value, label):
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < Decimal("0")
        or value != value.quantize(Decimal("0.01"))
    ):
        raise DatedStatutoryAtomicWriterError(
            f"Invalid {label}"
        )


async def write_verified_dated_salary_statutory_result(
    session: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
    actor_user_id: int,
) -> PayrollStatutoryResult:
    """Write a fully verified simple-salary dated result atomically.

    Unsupported employee-wide, special policy, severance and mixed
    income cases remain fail-closed through the verified planner.

    A savepoint provides all-or-nothing local insertion, including
    when the caller catches a raised exception. The outer transaction
    must still be committed by the caller.
    """
    _require_id(company_id, "company")
    _require_id(payroll_calculation_id, "payroll calculation")
    _require_id(actor_user_id, "actor")

    async with session.begin_nested():
        calculation = await session.scalar(
            select(PayrollCalculation)
            .where(
                PayrollCalculation.company_id == company_id,
                PayrollCalculation.id == payroll_calculation_id,
            )
            .with_for_update()
        )

        if calculation is None:
            raise DatedStatutoryAtomicWriterError(
                "Payroll calculation not found in company"
            )

        # Lock source rows to stop their modification during planning.
        sources = list(
            (
                await session.scalars(
                    select(PayrollCalculationLine)
                    .where(
                        PayrollCalculationLine.company_id
                        == company_id,
                        PayrollCalculationLine.payroll_calculation_id
                        == payroll_calculation_id,
                    )
                    .order_by(
                        PayrollCalculationLine.line_no,
                        PayrollCalculationLine.id,
                    )
                    .with_for_update()
                )
            ).all()
        )

        if not sources:
            raise DatedStatutoryAtomicWriterError(
                "Missing payroll income sources"
            )

        source_by_id = {row.id: row for row in sources}

        if len(source_by_id) != len(sources):
            raise DatedStatutoryAtomicWriterError(
                "Duplicate salary source identifier"
            )

        # Never overwrite a previously saved result.
        existing = await session.scalar(
            select(PayrollStatutoryResult)
            .where(
                PayrollStatutoryResult.company_id == company_id,
                PayrollStatutoryResult.payroll_calculation_id
                == payroll_calculation_id,
            )
            .with_for_update()
        )

        if existing is not None:
            raise DatedStatutoryAtomicWriterError(
                "Existing statutory result must not be overwritten"
            )

        # This planner reauthenticates source ownership, dated
        # statutory policies, component rates and totals.
        plan = await build_verified_dated_salary_statutory_plan(
            session,
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
        )

        if plan.currency_code != "UAH":
            raise DatedStatutoryAtomicWriterError(
                "Unsupported statutory currency"
            )

        if plan.gross_amount != Decimal(calculation.gross_amount):
            raise DatedStatutoryAtomicWriterError(
                "Payroll gross changed during planning"
            )

        for value, label in (
            (plan.gross_amount, "gross amount"),
            (
                plan.employee_withholding_amount,
                "employee withholding",
            ),
            (
                plan.employer_contribution_amount,
                "employer contribution",
            ),
            (plan.net_amount, "net amount"),
        ):
            _require_money(value, label)

        if plan.net_amount != (
            plan.gross_amount
            - plan.employee_withholding_amount
        ):
            raise DatedStatutoryAtomicWriterError(
                "Net pay reconciliation failed"
            )

        if not plan.lines:
            raise DatedStatutoryAtomicWriterError(
                "Statutory plan contains no lines"
            )

        rate_ids = {
            planned.source_rate_id
            for planned in plan.lines
        }

        rates = list(
            (
                await session.scalars(
                    select(PayrollStatutoryRate)
                    .where(
                        PayrollStatutoryRate.company_id == company_id,
                        PayrollStatutoryRate.id.in_(rate_ids),
                    )
                    .order_by(PayrollStatutoryRate.id)
                    .with_for_update()
                )
            ).all()
        )

        rates_by_id = {rate.id: rate for rate in rates}

        if set(rates_by_id) != rate_ids:
            raise DatedStatutoryAtomicWriterError(
                "Statutory rate provenance is missing"
            )

        checked_lines = []
        sources_per_component = {}

        # Validate every planned line before inserting the header.
        for expected_no, planned in enumerate(
            plan.lines, start=1
        ):
            if planned.line_no != expected_no:
                raise DatedStatutoryAtomicWriterError(
                    "Noncanonical statutory line numbering"
                )

            if planned.component not in COMPONENTS:
                raise DatedStatutoryAtomicWriterError(
                    "Unsupported statutory component"
                )

            if (
                planned.source_profile_id is not None
                or planned.source_base_rule_id is not None
            ):
                raise DatedStatutoryAtomicWriterError(
                    "Special tax policy cannot use simple writer"
                )

            rate = rates_by_id[planned.source_rate_id]

            if rate.component != planned.component:
                raise DatedStatutoryAtomicWriterError(
                    "Source rate belongs to another component"
                )

            if Decimal(rate.rate) != planned.rate:
                raise DatedStatutoryAtomicWriterError(
                    "Source rate changed after verification"
                )

            _require_money(planned.base_amount, "line base")
            _require_money(planned.amount, "line tax")

            if (
                type(planned.source_line_ids) is not tuple
                or not planned.source_line_ids
                or len(planned.source_line_ids)
                != len(set(planned.source_line_ids))
            ):
                raise DatedStatutoryAtomicWriterError(
                    "Invalid statutory source allocation"
                )

            linked = []

            for source_id in planned.source_line_ids:
                _require_id(source_id, "salary source")

                source = source_by_id.get(source_id)

                if source is None:
                    raise DatedStatutoryAtomicWriterError(
                        "Tax source belongs to another calculation"
                    )

                if (
                    source.line_type != "salary"
                    or source.source_effective_from is None
                    or source.source_effective_to is None
                    or source.source_salary_rate_id is None
                ):
                    raise DatedStatutoryAtomicWriterError(
                        "Unsupported or unverified salary source"
                    )

                if (
                    source.source_effective_from
                    < rate.effective_from
                    or (
                        rate.effective_to is not None
                        and source.source_effective_to
                        > rate.effective_to
                    )
                ):
                    raise DatedStatutoryAtomicWriterError(
                        "Income source outside statutory rate window"
                    )

                source_amount = Decimal(source.amount)
                _require_money(
                    source_amount, "salary source amount"
                )
                linked.append((source, source_amount))

            total = sum(
                (amount for _, amount in linked),
                Decimal("0.00"),
            )

            if total != planned.base_amount:
                raise DatedStatutoryAtomicWriterError(
                    "Source links do not reconcile to statutory base"
                )

            if (
                planned.amount
                != (planned.base_amount * planned.rate).quantize(
                    Decimal("0.01")
                )
            ):
                raise DatedStatutoryAtomicWriterError(
                    "Statutory amount/rate reconciliation failed"
                )

            sources_per_component.setdefault(
                planned.component, set()
            )

            overlap = (
                sources_per_component[planned.component]
                & set(planned.source_line_ids)
            )

            if overlap:
                raise DatedStatutoryAtomicWriterError(
                    "A salary source is duplicated in a component"
                )

            sources_per_component[planned.component].update(
                planned.source_line_ids
            )

            checked_lines.append((planned, rate, linked))

        if set(sources_per_component) != COMPONENTS:
            raise DatedStatutoryAtomicWriterError(
                "Exactly three statutory components required"
            )

        expected_sources = set(source_by_id)

        for component, actual_sources in sources_per_component.items():
            if actual_sources != expected_sources:
                raise DatedStatutoryAtomicWriterError(
                    f"Incomplete source coverage for {component}"
                )

        header = PayrollStatutoryResult(
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
            currency_code=plan.currency_code,
            gross_amount=plan.gross_amount,
            employee_withholding_amount=(
                plan.employee_withholding_amount
            ),
            employer_contribution_amount=(
                plan.employer_contribution_amount
            ),
            net_amount=plan.net_amount,
            calculated_by=actor_user_id,
        )

        session.add(header)
        await session.flush()

        for planned, rate, linked in checked_lines:
            line = PayrollStatutoryResultLine(
                company_id=company_id,
                payroll_statutory_result_id=header.id,
                line_no=planned.line_no,
                component=planned.component,
                base_amount=planned.base_amount,
                rate=planned.rate,
                amount=planned.amount,
                currency_code=plan.currency_code,
                source_rate_id=rate.id,
                source_tax_profile_id=None,
                source_base_rule_id=None,
                gross_base_amount=plan.gross_amount,
                benefit_amount_applied=Decimal("0.00"),
                exemption_applied=False,
                rate_effective_from=rate.effective_from,
                rate_effective_to=rate.effective_to,
            )

            session.add(line)
            await session.flush()

            for source, source_amount in linked:
                session.add(
                    PayrollStatutoryLineSource(
                        company_id=company_id,
                        statutory_line_id=line.id,
                        payroll_calculation_line_id=source.id,
                        source_amount=source_amount,
                        income_effective_from=(
                            source.source_effective_from
                        ),
                        income_effective_to=(
                            source.source_effective_to
                        ),
                    )
                )

        await session.flush()

        # Leave the outer transaction under caller control.
        # H1-B2-B2-A ATOMIC STATUTORY SEAL
        from app.models.payroll_statutory import (
            PayrollDatedStatutorySeal,
        )

        session.add(
            PayrollDatedStatutorySeal(
                company_id=company_id,
                payroll_statutory_result_id=header.id,
            )
        )
        await session.flush()

        return header
