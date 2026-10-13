"""D3-B2: non-posting payroll deduction accounting preflight.

This service checks candidate D1/D2 outputs. It does not independently
authenticate their provenance, authorize withholding, or post journals.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services.payroll_deduction_batch_limit_preview import (
    BatchLimitPreview,
)


class AccountingPreflightError(ValueError):
    pass


@dataclass(frozen=True)
class AccountingPreflightResult:
    company_id: int
    instruction_id: int
    requested_amount: Decimal
    candidate_permitted_amount: Decimal
    candidate_unapplied_amount: Decimal
    financial_execution_authorized: bool = False
    journal_posting_allowed: bool = False
    legal_authorization: str = "NOT_AUTHORIZED"


ZERO = Decimal("0.00")


def _money(value, field):
    if type(value) is not Decimal or not value.is_finite():
        raise AccountingPreflightError(
            f"{field} must be a finite Decimal"
        )

    if value < ZERO:
        raise AccountingPreflightError(
            f"{field} must not be negative"
        )

    if value * 100 != (value * 100).to_integral_value():
        raise AccountingPreflightError(
            f"{field} exceeds cent precision"
        )

    return value


def evaluate_accounting_preflight(
    *,
    company_id: int,
    instruction_id: int,
    requested_amount: Decimal,
    d1_preflight: object,
    d2_preview: BatchLimitPreview,
) -> AccountingPreflightResult:
    """Validate technical previews; never authorize journal posting."""

    if type(company_id) is not int or company_id <= 0:
        raise AccountingPreflightError("Invalid company")

    if type(instruction_id) is not int or instruction_id <= 0:
        raise AccountingPreflightError("Invalid instruction")

    requested = _money(requested_amount, "requested_amount")

    if getattr(
        d1_preflight,
        "financial_execution_authorized",
        None,
    ) is not False:
        raise AccountingPreflightError(
            "D1 must not authorize financial execution"
        )

    if type(d2_preview) is not BatchLimitPreview:
        raise AccountingPreflightError(
            "Expected a D2 BatchLimitPreview"
        )

    if d2_preview.financial_execution_authorized is not False:
        raise AccountingPreflightError(
            "D2 must not authorize financial execution"
        )

    if d2_preview.legal_authorization != "NOT_AUTHORIZED":
        raise AccountingPreflightError(
            "Unexpected legal authorization"
        )

    if type(d2_preview.lines) is not tuple:
        raise AccountingPreflightError("Invalid batch lines")

    total_requested = ZERO
    total_permitted = ZERO
    total_unapplied = ZERO
    seen = set()
    selected = None

    for line in d2_preview.lines:
        row_id = getattr(line, "instruction_id", None)

        if type(row_id) is not int or row_id <= 0:
            raise AccountingPreflightError("Invalid batch ID")

        if row_id in seen:
            raise AccountingPreflightError("Duplicate batch ID")

        seen.add(row_id)

        row_requested = _money(
            getattr(line, "requested_amount", None),
            "line.requested_amount",
        )
        row_permitted = _money(
            getattr(line, "permitted_amount", None),
            "line.permitted_amount",
        )
        row_unapplied = _money(
            getattr(line, "unapplied_amount", None),
            "line.unapplied_amount",
        )

        if row_permitted + row_unapplied != row_requested:
            raise AccountingPreflightError(
                "Batch line conservation failed"
            )

        total_requested += row_requested
        total_permitted += row_permitted
        total_unapplied += row_unapplied

        if row_id == instruction_id:
            selected = line

    if selected is None:
        raise AccountingPreflightError(
            "Instruction missing from D2 preview"
        )

    if _money(
        selected.requested_amount,
        "selected.requested_amount",
    ) != requested:
        raise AccountingPreflightError(
            "Requested amount mismatch"
        )

    if total_requested != _money(
        d2_preview.total_requested,
        "total_requested",
    ):
        raise AccountingPreflightError(
            "Requested batch total mismatch"
        )

    if total_permitted != _money(
        d2_preview.total_permitted,
        "total_permitted",
    ):
        raise AccountingPreflightError(
            "Permitted batch total mismatch"
        )

    if total_unapplied != _money(
        d2_preview.total_unapplied,
        "total_unapplied",
    ):
        raise AccountingPreflightError(
            "Unapplied batch total mismatch"
        )

    remaining = _money(
        d2_preview.remaining_global_capacity,
        "remaining_global_capacity",
    )

    if remaining < ZERO:
        raise AccountingPreflightError(
            "Invalid remaining capacity"
        )

    return AccountingPreflightResult(
        company_id=company_id,
        instruction_id=instruction_id,
        requested_amount=requested,
        candidate_permitted_amount=selected.permitted_amount,
        candidate_unapplied_amount=selected.unapplied_amount,
    )
