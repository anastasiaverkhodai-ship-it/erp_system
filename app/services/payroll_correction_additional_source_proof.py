"""Read-only consistency proof for additional payroll components.

Re-derives supplements, vacation pay and sick pay using existing ERP
services, then compares source IDs and financial values against the
original calculation.

This is NOT a historical immutability proof and does not authorize
creation of a correction revision or financial execution.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.models.payroll import PayrollCalculationLine

from app.services.payroll_supplement_service import (
    PayrollSupplementError,
    derive_supplement_lines,
)
from app.services.payroll_vacation_service import (
    PayrollVacationDerivationError,
    derive_vacation_pay_lines,
)
from app.services.payroll_sick_leave_service import (
    PayrollSickLeaveDerivationError,
    derive_sick_pay_lines,
)


class PayrollAdditionalSourceProofError(ValueError):
    pass


SOURCE_FIELDS = {
    "supplement": "source_supplement_id",
    "vacation_pay": "source_vacation_calculation_id",
    "sick_pay": "source_sick_leave_calculation_id",
}


def _read(row, field):
    if isinstance(row, dict):
        return row.get(field)
    return getattr(row, field, None)


def _text(value):
    return str(getattr(value, "value", value))


def _decimal(value, field):
    if value is None or isinstance(value, bool):
        raise PayrollAdditionalSourceProofError(
            f"Missing or invalid {field}"
        )

    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PayrollAdditionalSourceProofError(
            f"Invalid {field}"
        ) from exc

    if not result.is_finite():
        raise PayrollAdditionalSourceProofError(
            f"Non-finite {field}"
        )

    return result


def _signature(row, kind):
    source_field = SOURCE_FIELDS[kind]

    actual_kind = _text(_read(row, "line_type"))

    if actual_kind != kind:
        raise PayrollAdditionalSourceProofError(
            f"Unexpected component type: {actual_kind}"
        )

    source_id = _read(row, source_field)

    if type(source_id) is not int or source_id <= 0:
        raise PayrollAdditionalSourceProofError(
            f"Missing {source_field}"
        )

    currency = _read(row, "currency_code")

    if not isinstance(currency, str) or not currency:
        raise PayrollAdditionalSourceProofError(
            "Missing component currency"
        )

    return (
        source_id,
        currency,
        _decimal(_read(row, "amount"), "amount"),
        _decimal(_read(row, "quantity"), "quantity"),
        _decimal(_read(row, "rate"), "rate"),
    )


def compare_additional_source_lines(
    *,
    kind: str,
    stored_lines: list,
    derived_lines: list,
) -> dict:
    """Fail if any component differs from its re-derived source."""

    if kind not in SOURCE_FIELDS:
        raise PayrollAdditionalSourceProofError(
            "Unsupported additional payroll component"
        )

    if not isinstance(stored_lines, list):
        raise PayrollAdditionalSourceProofError(
            "Stored lines must be a list"
        )

    if not isinstance(derived_lines, list):
        raise PayrollAdditionalSourceProofError(
            "Derived lines must be a list"
        )

    original = Counter(
        _signature(row, kind)
        for row in stored_lines
    )

    recalculated = Counter(
        _signature(row, kind)
        for row in derived_lines
    )

    if original != recalculated:
        raise PayrollAdditionalSourceProofError(
            f"{kind} source provenance mismatch"
        )

    return {
        "component": kind,
        "stored_line_count": len(stored_lines),
        "derived_line_count": len(derived_lines),
        "source_values_reconciled": True,
        "historical_source_immutability_verified": False,
        "automatic_revision_creation_allowed": False,
        "financial_execution_allowed": False,
    }


async def verify_additional_payroll_sources(
    db,
    *,
    company_id: int,
    payroll_input,
    period,
    salary_slices: list,
    original_payroll_calculation_id: int,
) -> dict:
    """Compare original stored values with current ERP derivation.

    The caller must already hold the appropriate payroll mutation lock.
    This service does not commit or change database records.
    """

    if (
        payroll_input.company_id != company_id
        or period.company_id != company_id
        or payroll_input.payroll_period_id != period.id
    ):
        raise PayrollAdditionalSourceProofError(
            "Payroll source identity mismatch"
        )

    original_lines = (
        await db.scalars(
            select(PayrollCalculationLine)
            .where(
                PayrollCalculationLine.company_id == company_id,
                PayrollCalculationLine.payroll_calculation_id
                == original_payroll_calculation_id,
            )
            .order_by(
                PayrollCalculationLine.line_no,
                PayrollCalculationLine.id,
            )
        )
    ).all()

    grouped = {
        kind: []
        for kind in SOURCE_FIELDS
    }

    for line in original_lines:
        kind = _text(line.line_type)

        if kind in grouped:
            grouped[kind].append(line)

    try:
        supplement_lines, _ = await derive_supplement_lines(
            db,
            company_id=company_id,
            payroll_input=payroll_input,
            period=period,
            salary_slices=salary_slices,
            strict=True,
        )

        vacation_lines = await derive_vacation_pay_lines(
            db,
            company_id=company_id,
            payroll_input=payroll_input,
            period=period,
        )

        sick_lines = await derive_sick_pay_lines(
            db,
            company_id=company_id,
            payroll_input=payroll_input,
            period=period,
        )
    except (
        PayrollSupplementError,
        PayrollVacationDerivationError,
        PayrollSickLeaveDerivationError,
    ) as exc:
        raise PayrollAdditionalSourceProofError(
            "Cannot verify additional source: " + str(exc)
        ) from exc

    results = {}

    for kind, derived in (
        ("supplement", supplement_lines),
        ("vacation_pay", vacation_lines),
        ("sick_pay", sick_lines),
    ):
        results[kind] = compare_additional_source_lines(
            kind=kind,
            stored_lines=grouped[kind],
            derived_lines=list(derived),
        )

    return {
        "schema": "payroll-additional-source-proof-v1",
        "components": results,
        "current_source_values_reconciled": True,
        "historical_source_immutability_verified": False,
        "complete_payroll_provenance_verified": False,
        "automatic_revision_creation_allowed": False,
        "financial_execution_allowed": False,
    }
