"""Read-only re-derivation of an original minimum-wage assessment.

Matches a saved payroll assessment against the CURRENT ERP derivation.

This proves current-source consistency, NOT historical immutability.
It cannot authorize correction revisions, posting or payments.
Transaction and source-lock ownership remain with the caller.
"""

from decimal import Decimal

from app.services.payroll_minimum_wage_service import (
    MinimumWageError,
    derive_minimum_wage_assessment,
)
from app.services.payroll_correction_minimum_wage_proof import (
    PayrollMinimumWageProofError,
    verify_saved_minimum_wage_assessment,
)


class PayrollMinimumWageRederivationError(ValueError):
    pass


def _type(value):
    return str(getattr(value, "value", value))


def _decimal(value):
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise PayrollMinimumWageRederivationError(
            "Invalid payroll monetary value"
        ) from exc

    if not result.is_finite():
        raise PayrollMinimumWageRederivationError(
            "Non-finite payroll monetary value"
        )

    return result


def _line_dict(row):
    if isinstance(row, dict):
        return dict(row)

    return {
        column.key: getattr(row, column.key)
        for column in row.__mapper__.columns
    }


async def verify_original_minimum_wage_rederivation(
    db,
    *,
    company_id,
    period,
    payroll_input,
    salary_slices,
    original_calculation,
    original_lines,
):
    """Re-run ERP minimum-wage assessment without writing records."""

    if (
        original_calculation.company_id != company_id
        or payroll_input.company_id != company_id
        or period.company_id != company_id
        or original_calculation.payroll_input_id
        != payroll_input.id
        or original_calculation.payroll_period_id
        != period.id
        or payroll_input.payroll_period_id
        != period.id
        or original_calculation.employment_contract_id
        != payroll_input.employment_contract_id
    ):
        raise PayrollMinimumWageRederivationError(
            "Payroll correction source identity mismatch"
        )

    if not salary_slices or not original_lines:
        raise PayrollMinimumWageRederivationError(
            "Original payroll sources are required"
        )

    if any(
        item.company_id != company_id
        or item.payroll_input_id != payroll_input.id
        for item in salary_slices
    ):
        raise PayrollMinimumWageRederivationError(
            "Salary slice identity mismatch"
        )

    lines = [_line_dict(row) for row in original_lines]

    if any(
        line.get("company_id", company_id) != company_id
        or line.get(
            "payroll_calculation_id",
            original_calculation.id,
        ) != original_calculation.id
        for line in lines
    ):
        raise PayrollMinimumWageRederivationError(
            "Original calculation line identity mismatch"
        )

    if any(
        line.get("currency_code") != original_calculation.currency_code
        for line in lines
    ):
        raise PayrollMinimumWageRederivationError(
            "Original line currency mismatch"
        )

    total = sum(
        (_decimal(line.get("amount")) for line in lines),
        Decimal("0.00"),
    )

    if total != _decimal(original_calculation.gross_amount):
        raise PayrollMinimumWageRederivationError(
            "Original calculation gross reconciliation mismatch"
        )

    salary_types = {
        _type(item.salary_rate_type)
        for item in salary_slices
    }

    if len(salary_types) != 1:
        raise PayrollMinimumWageRederivationError(
            "Original salary method is ambiguous"
        )

    original_assessment = (
        {k: v for k, v in (original_calculation.minimum_wage_assessment or {}).items() if k != "correction_basis"}
    )

    try:
        saved_proof = verify_saved_minimum_wage_assessment(
            assessment=original_assessment,
            calculation_lines=lines,
            period_start=period.start_date,
            period_end=period.end_date,
            expected_salary_rate_type=next(iter(salary_types)),
        )
    except PayrollMinimumWageProofError as exc:
        raise PayrollMinimumWageRederivationError(
            "Saved minimum wage assessment is inconsistent: "
            + str(exc)
        ) from exc

    earning_lines = []

    for line in lines:
        line_type = _type(line.get("line_type"))

        if line_type == "minimum_wage":
            continue

        line["line_type"] = line_type
        earning_lines.append(line)

    try:
        derived_assessment, derived_top_up = (
            await derive_minimum_wage_assessment(
                db,
                company_id=company_id,
                period=period,
                payroll_input=payroll_input,
                salary_slices=salary_slices,
                lines=earning_lines,
                context=original_assessment["request"],
            )
        )
    except MinimumWageError as exc:
        raise PayrollMinimumWageRederivationError(
            "Minimum wage re-derivation failed: "
            + str(exc)
        ) from exc

    if derived_assessment != original_assessment:
        raise PayrollMinimumWageRederivationError(
            "Original minimum wage assessment differs "
            "from current ERP re-derivation"
        )

    stored_top_up = [
        line
        for line in lines
        if _type(line.get("line_type")) == "minimum_wage"
    ]

    if derived_top_up is None:
        if stored_top_up:
            raise PayrollMinimumWageRederivationError(
                "Unexpected original minimum wage top-up"
            )
    else:
        if len(stored_top_up) != 1:
            raise PayrollMinimumWageRederivationError(
                "Original minimum wage top-up line is missing"
            )

        stored = stored_top_up[0]

        for name in ("quantity", "rate", "amount"):
            if _decimal(stored.get(name)) != _decimal(
                derived_top_up.get(name)
            ):
                raise PayrollMinimumWageRederivationError(
                    "Original minimum wage top-up differs "
                    "from current ERP re-derivation"
                )

        for name in (
            "currency_code",
            "source_effective_from",
            "source_effective_to",
        ):
            if stored.get(name) != derived_top_up.get(name):
                raise PayrollMinimumWageRederivationError(
                    "Original minimum wage line provenance mismatch"
                )

    return {
        "schema": "payroll-minimum-wage-rederivation-proof-v1",
        "saved_assessment_consistent": saved_proof[
            "saved_assessment_consistent"
        ],
        "current_erp_rederivation_reconciled": True,
        "minimum_wage_line_count": len(stored_top_up),
        "historical_source_immutability_verified": False,
        "complete_payroll_provenance_verified": False,
        "automatic_revision_creation_allowed": False,
        "financial_execution_allowed": False,
    }
