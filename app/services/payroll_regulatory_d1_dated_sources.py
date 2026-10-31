"""Internal dated D1 financial source reconciliation.

This module traces frozen statutory lines to dated calculation
line sources. It is not a statutory D1/D6 form mapping.

No writing, approval, filing, or guessed normative categories.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.models.payroll import PayrollCalculationLine
from app.models.payroll_statutory import (
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
    PayrollStatutoryLineSource,
)
from app.services.payroll_regulatory_d1_review_projection import (
    build_verified_d1_review,
)


class D1DatedSourceError(ValueError):
    pass


ZERO = Decimal("0.00")
USC = "unified_social_contribution"


def decimal_amount(value):
    if value is None or isinstance(value, bool):
        raise D1DatedSourceError("Missing monetary amount")

    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise D1DatedSourceError(
            "Invalid monetary amount"
        ) from exc

    if not result.is_finite():
        raise D1DatedSourceError(
            "Non-finite monetary amount"
        )

    if result.as_tuple().exponent < -2:
        raise D1DatedSourceError(
            "Invalid monetary precision"
        )

    return result


def verify_source_dates(
    *,
    effective_from,
    effective_to,
):
    if (
        not isinstance(effective_from, date)
        or not isinstance(effective_to, date)
        or effective_from > effective_to
    ):
        raise D1DatedSourceError(
            "Invalid dated statutory source interval"
        )


def dated_source_control(
    *,
    statutory_line_id,
    statutory_amount,
    source_records,
):
    """Validate provenance; do not equate tax to taxable source base.

    source_amount belongs to the dated source of a statutory line;
    statutory_amount is the resulting calculated contribution.
    The two quantities are not assumed to be identical.
    """

    if type(statutory_line_id) is not int or statutory_line_id <= 0:
        raise D1DatedSourceError(
            "Invalid statutory line identifier"
        )

    contribution = decimal_amount(statutory_amount)

    if contribution < ZERO:
        raise D1DatedSourceError(
            "Negative statutory contribution"
        )

    if not isinstance(source_records, list):
        raise D1DatedSourceError(
            "Invalid dated source records"
        )

    seen = set()
    output = []
    source_total = ZERO

    for item in source_records:
        if not isinstance(item, dict):
            raise D1DatedSourceError(
                "Invalid dated source item"
            )

        identifier = item.get("source_id")

        if (
            type(identifier) is not int
            or identifier <= 0
            or identifier in seen
        ):
            raise D1DatedSourceError(
                "Duplicate or invalid dated source"
            )

        seen.add(identifier)

        verify_source_dates(
            effective_from=item.get("effective_from"),
            effective_to=item.get("effective_to"),
        )

        amount = decimal_amount(item.get("source_amount"))

        if amount < ZERO:
            raise D1DatedSourceError(
                "Negative dated source amount"
            )

        source_total += amount

        output.append({
            "source_id": identifier,
            "payroll_calculation_line_id": item[
                "payroll_calculation_line_id"
            ],
            "income_effective_from": item[
                "effective_from"
            ].isoformat(),
            "income_effective_to": item[
                "effective_to"
            ].isoformat(),
            "source_amount": str(amount),
        })

    output.sort(
        key=lambda row: (
            row["income_effective_from"],
            row["income_effective_to"],
            row["source_id"],
        )
    )

    return {
        "statutory_line_id": statutory_line_id,
        "statutory_contribution_amount": str(contribution),
        "dated_source_count": len(output),
        "dated_source_amount_total": str(source_total),
        "dated_source_coverage": (
            "present" if output else "missing"
        ),
        "dated_sources": output,
    }


async def build_d1_dated_source_review(
    db,
    *,
    company_id,
    payroll_period_id,
):
    """Trace verified D1 source rows to actual PostgreSQL history."""

    review = await build_verified_d1_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    calculation_ids = {
        row["payroll_calculation_id"]
        for row in review["rows"]
    }

    statutory_results = (
        await db.scalars(
            select(PayrollStatutoryResult)
            .where(
                PayrollStatutoryResult.company_id == company_id,
                PayrollStatutoryResult.payroll_calculation_id.in_(
                    calculation_ids
                ),
            )
        )
    ).all()

    result_to_calculation = {}

    for result in statutory_results:
        if result.id in result_to_calculation:
            raise D1DatedSourceError(
                "Duplicate statutory result"
            )

        result_to_calculation[result.id] = (
            result.payroll_calculation_id
        )

    if set(result_to_calculation.values()) != calculation_ids:
        raise D1DatedSourceError(
            "Incomplete D1 statutory result coverage"
        )

    statutory_lines = (
        await db.scalars(
            select(PayrollStatutoryResultLine)
            .where(
                PayrollStatutoryResultLine.company_id == company_id,
                PayrollStatutoryResultLine.payroll_statutory_result_id.in_(
                    list(result_to_calculation)
                ),
                PayrollStatutoryResultLine.component == USC,
            )
        )
    ).all()

    line_ids = {line.id for line in statutory_lines}

    sources = []

    if line_ids:
        sources = (
            await db.scalars(
                select(PayrollStatutoryLineSource)
                .where(
                    PayrollStatutoryLineSource.company_id == company_id,
                    PayrollStatutoryLineSource.statutory_line_id.in_(
                        line_ids
                    ),
                )
            )
        ).all()

    calculation_lines = {}

    source_calculation_line_ids = {
        item.payroll_calculation_line_id
        for item in sources
    }

    if source_calculation_line_ids:
        calculation_line_records = (
            await db.scalars(
                select(PayrollCalculationLine)
                .where(
                    PayrollCalculationLine.company_id == company_id,
                    PayrollCalculationLine.id.in_(
                        source_calculation_line_ids
                    ),
                )
            )
        ).all()

        calculation_lines = {
            line.id: line
            for line in calculation_line_records
        }

        if len(calculation_lines) != len(
            calculation_line_records
        ):
            raise D1DatedSourceError(
                "Duplicate payroll calculation line"
            )

    grouped = defaultdict(list)
    source_ids = set()

    for item in sources:
        if item.id in source_ids:
            raise D1DatedSourceError(
                "Duplicate dated statutory source"
            )

        source_ids.add(item.id)

        if item.statutory_line_id not in line_ids:
            raise D1DatedSourceError(
                "Dated source references another statutory line"
            )

        calc_line = calculation_lines.get(
            item.payroll_calculation_line_id
        )

        if calc_line is None:
            raise D1DatedSourceError(
                "Dated source payroll calculation line missing"
            )

        statutory_line = next(
            (
                line
                for line in statutory_lines
                if line.id == item.statutory_line_id
            ),
            None,
        )

        if statutory_line is None:
            raise D1DatedSourceError(
                "Statutory line missing"
            )

        calculation_id = result_to_calculation[
            statutory_line.payroll_statutory_result_id
        ]

        if calc_line.payroll_calculation_id != calculation_id:
            raise D1DatedSourceError(
                "Cross-calculation statutory source"
            )

        grouped[item.statutory_line_id].append({
            "source_id": item.id,
            "payroll_calculation_line_id":
                item.payroll_calculation_line_id,
            "effective_from": item.income_effective_from,
            "effective_to": item.income_effective_to,
            "source_amount": item.source_amount,
        })

    controls = []
    missing_count = 0
    contribution_total = ZERO

    for line in sorted(
        statutory_lines,
        key=lambda entry: entry.id,
    ):
        control = dated_source_control(
            statutory_line_id=line.id,
            statutory_amount=line.amount,
            source_records=grouped[line.id],
        )

        if control["dated_source_coverage"] == "missing":
            missing_count += 1

        contribution_total += decimal_amount(line.amount)

        control["payroll_calculation_id"] = (
            result_to_calculation[
                line.payroll_statutory_result_id
            ]
        )

        controls.append(control)

    expected_total = decimal_amount(
        review["employer_contribution_total"]
    )

    if contribution_total != expected_total:
        raise D1DatedSourceError(
            "D1 statutory contribution total mismatch"
        )

    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "report_kind": "d1",
        "review_only": True,
        "official_statutory_generation": "BLOCKED",
        "d1_financial_reconciliation": "PASS",
        "dated_provenance_status": (
            "COMPLETE" if not missing_count else "INCOMPLETE"
        ),
        "dated_statutory_line_count": len(controls),
        "missing_dated_source_count": missing_count,
        "employer_contribution_total": str(contribution_total),
        "dated_controls": controls,
        "d6_control": {
            "status": "NOT_VERIFIED",
            "reason": (
                "Independent D6 applicability and approved "
                "mapping are not supplied by this D1 ledger"
            ),
        },
    }
