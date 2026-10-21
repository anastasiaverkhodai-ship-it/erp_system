"""Read-only integration of payroll calculation and severance evidence.

This module does not approve tax policies, compute tax payable,
write snapshots, or authorize financial operations.
"""

from datetime import date

from app.services.payroll_calculation_service import (
    get_payroll_calculation,
    list_payroll_calculation_lines,
)
from app.services.payroll_severance_income_classification import (
    split_payroll_income,
)
from app.services.payroll_severance_tax_classification import (
    classify_severance_tax_bases,
)
from app.services.payroll_severance_statutory_snapshot import (
    build_severance_statutory_snapshot,
    verify_severance_statutory_snapshot,
)


class SeveranceSnapshotIntegrationError(ValueError):
    """Missing or invalid integration input."""


async def preview_severance_statutory_snapshot(
    session,
    *,
    company_id: int,
    payroll_calculation_id: int,
    income_date: date,
    policies,
):
    """Construct, but DO NOT PERSIST, a severance evidence snapshot.

    The policies argument is explicit caller-supplied classification
    evidence. This function does not certify its legal authenticity.
    """

    if type(company_id) is not int or company_id <= 0:
        raise SeveranceSnapshotIntegrationError(
            "Valid company_id is required"
        )

    if (
        type(payroll_calculation_id) is not int
        or payroll_calculation_id <= 0
    ):
        raise SeveranceSnapshotIntegrationError(
            "Valid payroll_calculation_id is required"
        )

    if type(income_date) is not date:
        raise SeveranceSnapshotIntegrationError(
            "Explicit income_date is required"
        )

    if policies is None:
        raise SeveranceSnapshotIntegrationError(
            "Explicit dated severance policy evidence is required"
        )

    # Resolve the calculation using the existing company-scoped
    # service, not a raw unscoped primary-key lookup.
    calculation = await get_payroll_calculation(
        session,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    if calculation is None:
        raise SeveranceSnapshotIntegrationError(
            "Payroll calculation not found"
        )

    if (
        calculation.company_id != company_id
        or calculation.id != payroll_calculation_id
    ):
        raise SeveranceSnapshotIntegrationError(
            "Payroll calculation ownership mismatch"
        )

    lines = await list_payroll_calculation_lines(
        session,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    income_split = split_payroll_income(
        gross_amount=calculation.gross_amount,
        currency_code=calculation.currency_code,
        lines=lines,
    )

    if not income_split.severance_source_ids:
        raise SeveranceSnapshotIntegrationError(
            "Payroll calculation has no documented severance"
        )

    # No fallback or guessed policy. Missing, expired or ambiguous
    # policy evidence is rejected by the classification component.
    tax_bases = classify_severance_tax_bases(
        split=income_split,
        income_date=income_date,
        policies=policies,
    )

    snapshot = build_severance_statutory_snapshot(
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
        income_date=income_date,
        income_split=income_split,
        tax_bases=tax_bases,
        currency_code=calculation.currency_code,
    )

    verify_severance_statutory_snapshot(snapshot)

    return snapshot
