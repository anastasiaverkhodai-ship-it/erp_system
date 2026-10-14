"""Fail-closed source coverage assessment for payroll corrections.

This component records missing proof. It never authorizes revision
creation, accounting postings, payments, or finalized-input mutation.

Coverage is descriptive, not a substitute for historical source proof.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


class PayrollProvenanceCoverageError(ValueError):
    pass


def _amount(value: Any) -> Decimal:
    """Read exact decimal values from canonical Source Evidence V1.

    The source snapshot serializer represents Decimal objects as:
        {"decimal": "10000.00"}

    Ordinary Decimal, int and decimal-string values remain supported.
    Unexpected mappings, floats, booleans and non-finite values are
    rejected rather than silently coerced.
    """

    if isinstance(value, dict):
        if set(value) != {"decimal"}:
            raise PayrollProvenanceCoverageError(
                "Invalid canonical monetary object in source snapshot"
            )

        value = value["decimal"]

        if not isinstance(value, str):
            raise PayrollProvenanceCoverageError(
                "Canonical monetary value must be a string"
            )

    if (
        isinstance(value, bool)
        or isinstance(value, float)
        or not isinstance(value, (Decimal, int, str))
    ):
        raise PayrollProvenanceCoverageError(
            "Invalid monetary type in source snapshot"
        )

    if isinstance(value, str) and not value.strip():
        raise PayrollProvenanceCoverageError(
            "Empty monetary value in source snapshot"
        )

    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PayrollProvenanceCoverageError(
            "Invalid monetary amount in source snapshot"
        ) from exc

    if not result.is_finite():
        raise PayrollProvenanceCoverageError(
            "Non-finite monetary amount in source snapshot"
        )

    return result


def assess_payroll_correction_source_coverage(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Identify gaps without granting financial authorization."""

    if not isinstance(snapshot, Mapping):
        raise PayrollProvenanceCoverageError(
            "Source snapshot must be a mapping"
        )

    if snapshot.get("schema") != "payroll-correction-source-v1":
        raise PayrollProvenanceCoverageError(
            "Unsupported payroll source evidence schema"
        )

    calculation = snapshot.get("calculation")
    payroll_input = snapshot.get("payroll_input")
    slices = snapshot.get("salary_slices")
    lines = snapshot.get("calculation_lines")

    if not isinstance(calculation, Mapping):
        raise PayrollProvenanceCoverageError(
            "Calculation snapshot is missing"
        )

    if not isinstance(payroll_input, Mapping):
        raise PayrollProvenanceCoverageError(
            "Payroll input snapshot is missing"
        )

    if not isinstance(slices, list):
        raise PayrollProvenanceCoverageError(
            "Salary slices must be a list"
        )

    if not isinstance(lines, list):
        raise PayrollProvenanceCoverageError(
            "Calculation lines must be a list"
        )

    blockers = set()

    if not slices:
        blockers.add("missing_salary_slices")

    if not lines:
        blockers.add("missing_calculation_lines")

    currency = calculation.get("currency_code")

    if not isinstance(currency, str) or not currency:
        blockers.add("missing_calculation_currency")

    salary_rate_ids = set()

    for item in slices:
        if not isinstance(item, Mapping):
            raise PayrollProvenanceCoverageError(
                "Invalid salary slice snapshot"
            )

        if item.get("currency_code") != currency:
            blockers.add("salary_slice_currency_mismatch")

        rate_id = item.get("salary_rate_id")

        if rate_id is None:
            blockers.add("salary_slice_without_rate_reference")
        else:
            salary_rate_ids.add(str(rate_id))

    total = Decimal("0.00")
    line_types = set()

    for item in lines:
        if not isinstance(item, Mapping):
            raise PayrollProvenanceCoverageError(
                "Invalid calculation line snapshot"
            )

        line_type = item.get("line_type")
        line_types.add(str(line_type))

        if item.get("currency_code") != currency:
            blockers.add("calculation_line_currency_mismatch")

        total += _amount(item.get("amount"))

        if line_type == "salary":
            reference = item.get("source_salary_rate_id")

            if reference is None:
                blockers.add("salary_line_without_rate_reference")
            elif str(reference) not in salary_rate_ids:
                blockers.add("salary_line_rate_not_in_input_slices")
        else:
            blockers.add(
                "additional_calculation_component_requires_proof"
            )

    if total != _amount(calculation.get("gross_amount")):
        blockers.add("calculation_gross_mismatch")

    blockers.update({
        "historical_salary_rate_provenance_unverified",
        "supplement_source_provenance_unverified",
        "vacation_source_provenance_unverified",
        "sick_leave_source_provenance_unverified",
        "minimum_wage_statutory_source_unverified",
        "source_freeze_and_transaction_proof_unverified",
    })

    return {
        "schema": "payroll-correction-provenance-coverage-v1",
        "salary_slice_count": len(slices),
        "calculation_line_count": len(lines),
        "line_types": sorted(line_types),
        "blockers": sorted(blockers),
        "blocker_count": len(blockers),
        "coverage_complete": False,
        "automatic_revision_creation_allowed": False,
        "financial_execution_allowed": False,
    }
