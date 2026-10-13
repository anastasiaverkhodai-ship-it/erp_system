"""D4-B1: non-executing accounting-to-remittance safety bridge.

Inputs are technical previews, not proof of withheld funds or payment.
This service does not read/write the database, post journals,
allocate remittances or authorize bank transfers.
"""

from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0.00")


class RemittanceReadinessError(ValueError):
    """A technical remittance input is missing or inconsistent."""


@dataclass(frozen=True)
class RemittanceReadiness:
    company_id: int
    instruction_id: int
    candidate_permitted_amount: Decimal
    candidate_unapplied_amount: Decimal
    technical_reconciled: bool
    withheld_funds_verified: bool = False
    bank_payment_verified: bool = False
    financial_execution_authorized: bool = False
    journal_posting_allowed: bool = False
    payment_execution_allowed: bool = False
    legal_authorization: str = "NOT_AUTHORIZED"


def _money(value, name):
    if type(value) is not Decimal or not value.is_finite():
        raise RemittanceReadinessError(
            f"{name} must be a finite Decimal"
        )

    if value < ZERO:
        raise RemittanceReadinessError(
            f"{name} must be nonnegative"
        )

    if value * 100 != (value * 100).to_integral_value():
        raise RemittanceReadinessError(
            f"{name} exceeds cent precision"
        )

    return value


def _id(value, name):
    if type(value) is not int or value <= 0:
        raise RemittanceReadinessError(
            f"{name} must be a positive integer"
        )

    return value


def evaluate_remittance_readiness(
    *,
    company_id: int,
    instruction_id: int,
    accounting_orchestration: object,
    remittance_reconciliation: object,
) -> RemittanceReadiness:
    """Compare two technical outputs without authorizing payment.

    This function intentionally does not convert a candidate deduction
    amount into verified withheld funds or a payment obligation.

    Actual provenance of the inputs must be established by a separate
    database-backed orchestration service before any operational use.
    """

    _id(company_id, "company_id")
    _id(instruction_id, "instruction_id")

    accounting = accounting_orchestration
    reconciliation = remittance_reconciliation

    if accounting is None or reconciliation is None:
        raise RemittanceReadinessError(
            "Accounting and remittance results are required"
        )

    if getattr(accounting, "company_id", None) != company_id:
        raise RemittanceReadinessError(
            "Accounting company mismatch"
        )

    if getattr(reconciliation, "company_id", None) != company_id:
        raise RemittanceReadinessError(
            "Remittance company mismatch"
        )

    if getattr(
        reconciliation, "instruction_id", None
    ) != instruction_id:
        raise RemittanceReadinessError(
            "Remittance instruction mismatch"
        )

    for obj, field in (
        (accounting, "financial_execution_authorized"),
        (accounting, "journal_posting_allowed"),
        (reconciliation, "payment_execution_allowed"),
        (reconciliation, "payroll_posting_allowed"),
        (reconciliation, "withheld_funds_verified"),
        (reconciliation, "bank_payment_verified"),
    ):
        if getattr(obj, field, None) is not False:
            raise RemittanceReadinessError(
                f"{field} must remain False"
            )

    for obj in (accounting, reconciliation):
        if getattr(
            obj, "legal_authorization", None
        ) != "NOT_AUTHORIZED":
            raise RemittanceReadinessError(
                "Unexpected legal authorization"
            )

    technical = getattr(
        reconciliation, "technical_reconciled", None
    )

    if technical is not True:
        raise RemittanceReadinessError(
            "Technical remittance reconciliation required"
        )

    rows = getattr(
        accounting, "accounting_preflights", None
    )

    if type(rows) is not tuple or not rows:
        raise RemittanceReadinessError(
            "Accounting preflight rows required"
        )

    matching = []
    seen = set()

    for row in rows:
        row_id = _id(
            getattr(row, "instruction_id", None),
            "accounting instruction",
        )

        if row_id in seen:
            raise RemittanceReadinessError(
                "Duplicate accounting instruction"
            )

        seen.add(row_id)

        if getattr(row, "company_id", None) != company_id:
            raise RemittanceReadinessError(
                "Accounting line company mismatch"
            )

        if getattr(
            row, "financial_execution_authorized", None
        ) is not False:
            raise RemittanceReadinessError(
                "Accounting line financial authorization must be False"
            )

        if getattr(
            row, "journal_posting_allowed", None
        ) is not False:
            raise RemittanceReadinessError(
                "Accounting line posting must remain blocked"
            )

        if getattr(
            row, "legal_authorization", None
        ) != "NOT_AUTHORIZED":
            raise RemittanceReadinessError(
                "Unexpected accounting line authorization"
            )

        requested = _money(
            getattr(row, "requested_amount", None),
            "requested_amount",
        )

        permitted = _money(
            getattr(row, "candidate_permitted_amount", None),
            "candidate_permitted_amount",
        )

        unapplied = _money(
            getattr(row, "candidate_unapplied_amount", None),
            "candidate_unapplied_amount",
        )

        if permitted + unapplied != requested:
            raise RemittanceReadinessError(
                "Candidate amount conservation failed"
            )

        if row_id == instruction_id:
            matching.append((permitted, unapplied))

    if len(matching) != 1:
        raise RemittanceReadinessError(
            "Exactly one matching accounting instruction required"
        )

    permitted, unapplied = matching[0]

    # No monetary equality is asserted between candidate limits
    # and actual withheld funds: they represent different facts.
    return RemittanceReadiness(
        company_id=company_id,
        instruction_id=instruction_id,
        candidate_permitted_amount=permitted,
        candidate_unapplied_amount=unapplied,
        technical_reconciled=True,
    )
