"""Fail-closed integrity verification for saved 2026 minimum wage.

This checks internal consistency of an existing assessment.
It does not independently prove all historical earnings, statutory
source authenticity or transaction isolation. No writes are made.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.payroll_minimum_wage_service import (
    MinimumWageError,
    normalize_minimum_wage_context,
)


class PayrollMinimumWageProofError(ValueError):
    pass


RULE_VERSION = "UA-4695-IX-2026-v1"
RULE_REFERENCE = "https://zakon.rada.gov.ua/laws/show/4695-IX"


def _decimal(value):
    if (
        isinstance(value, bool)
        or isinstance(value, float)
        or not isinstance(value, (Decimal, str, int))
    ):
        raise PayrollMinimumWageProofError(
            "Invalid minimum-wage monetary type"
        )

    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PayrollMinimumWageProofError(
            "Invalid minimum-wage monetary value"
        ) from exc

    if not result.is_finite():
        raise PayrollMinimumWageProofError(
            "Non-finite minimum-wage monetary value"
        )

    return result


def _read(row, name):
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _line_type(row):
    value = _read(row, "line_type")
    return getattr(value, "value", value)


def verify_saved_minimum_wage_assessment(
    *,
    assessment,
    calculation_lines,
    period_start,
    period_end,
    expected_salary_rate_type,
):
    """Validate saved assessment and its posted calculation lines.

    This is a consistency proof only: revision creation remains blocked.
    """

    if not isinstance(assessment, dict):
        raise PayrollMinimumWageProofError(
            "Saved minimum-wage assessment is missing"
        )

    if (
        not isinstance(period_start, date)
        or not isinstance(period_end, date)
        or period_start > period_end
        or period_start < date(2026, 1, 1)
        or period_end > date(2026, 12, 31)
    ):
        raise PayrollMinimumWageProofError(
            "Minimum-wage period is outside the verified 2026 rule"
        )

    if assessment.get("status") != "assessed":
        raise PayrollMinimumWageProofError(
            "Minimum-wage assessment is not assessed"
        )

    if assessment.get("rule_version") != RULE_VERSION:
        raise PayrollMinimumWageProofError(
            "Minimum-wage rule version mismatch"
        )

    if assessment.get("source_reference") != RULE_REFERENCE:
        raise PayrollMinimumWageProofError(
            "Minimum-wage rule reference mismatch"
        )

    rate_type = getattr(
        expected_salary_rate_type,
        "value",
        expected_salary_rate_type,
    )

    if rate_type not in ("monthly", "hourly"):
        raise PayrollMinimumWageProofError(
            "Unsupported salary rate type"
        )

    if assessment.get("rate_type") != rate_type:
        raise PayrollMinimumWageProofError(
            "Saved salary rate type mismatch"
        )

    expected_rate = (
        Decimal("8647")
        if rate_type == "monthly"
        else Decimal("52")
    )

    if _decimal(assessment.get("minimum_rate")) != expected_rate:
        raise PayrollMinimumWageProofError(
            "Saved statutory minimum rate mismatch"
        )

    if not isinstance(calculation_lines, list):
        raise PayrollMinimumWageProofError(
            "Calculation lines must be a list"
        )

    monetary_fields = (
        "guaranteed_amount",
        "included_earnings",
        "excluded_earnings",
        "top_up",
    )

    amounts = {
        name: _decimal(assessment.get(name))
        for name in monetary_fields
    }

    if any(value < 0 for value in amounts.values()):
        raise PayrollMinimumWageProofError(
            "Negative minimum-wage assessment amount"
        )

    expected_top_up = max(
        Decimal("0"),
        amounts["guaranteed_amount"]
        - amounts["included_earnings"],
    )

    if amounts["top_up"] != expected_top_up:
        raise PayrollMinimumWageProofError(
            "Minimum-wage top-up reconciliation mismatch"
        )

    request = assessment.get("request")

    if not isinstance(request, dict):
        raise PayrollMinimumWageProofError(
            "Saved minimum-wage request is missing"
        )

    try:
        normalized_request = normalize_minimum_wage_context(
            request
        )
    except MinimumWageError as exc:
        raise PayrollMinimumWageProofError(
            "Saved minimum-wage context is invalid"
        ) from exc

    if normalized_request != request:
        raise PayrollMinimumWageProofError(
            "Saved minimum-wage request is not canonical"
        )

    if (
        type(assessment.get("ordinary_worked_minutes"))
        is not int
        or assessment["ordinary_worked_minutes"] < 0
    ):
        raise PayrollMinimumWageProofError(
            "Invalid ordinary worked minutes"
        )

    minimum_lines = [
        line
        for line in calculation_lines
        if _line_type(line) == "minimum_wage"
    ]

    if expected_top_up == 0:
        if minimum_lines:
            raise PayrollMinimumWageProofError(
                "Unexpected minimum-wage top-up line"
            )
    else:
        if len(minimum_lines) != 1:
            raise PayrollMinimumWageProofError(
                "Exactly one minimum-wage top-up line is required"
            )

        line = minimum_lines[0]

        if (
            _read(line, "currency_code") != "UAH"
            or _decimal(_read(line, "amount"))
            != expected_top_up
            or _decimal(_read(line, "rate"))
            != expected_top_up
            or _decimal(_read(line, "quantity"))
            != Decimal("1")
        ):
            raise PayrollMinimumWageProofError(
                "Minimum-wage top-up line mismatch"
            )

        if (
            _read(line, "source_effective_from")
            != period_start
            or _read(line, "source_effective_to")
            != period_end
        ):
            raise PayrollMinimumWageProofError(
                "Minimum-wage top-up effective dates mismatch"
            )

    return {
        "schema": "payroll-minimum-wage-assessment-proof-v1",
        "saved_assessment_consistent": True,
        "minimum_wage_line_count": len(minimum_lines),
        "rule_version": RULE_VERSION,
        "historical_earnings_provenance_verified": False,
        "complete_payroll_provenance_verified": False,
        "automatic_revision_creation_allowed": False,
        "financial_execution_allowed": False,
    }
