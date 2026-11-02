"""Severance financial-difference planning.

Inputs must be independently verified, dated severance-only financial
attributions. The planner does not establish their authenticity or legal
authority and performs no writes, payouts, tax refunds or deductions.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class SeveranceSettlementError(ValueError):
    pass


ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def amount(value, label, *, nonnegative=False):
    if isinstance(value, bool):
        raise SeveranceSettlementError(f"{label}: invalid money")
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result != result.quantize(CENT):
            raise SeveranceSettlementError(
                f"{label}: finite two-decimal money required"
            )
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise SeveranceSettlementError(
            f"{label}: invalid money"
        ) from exc
    if nonnegative and result < ZERO:
        raise SeveranceSettlementError(
            f"{label}: negative value"
        )
    return result


@dataclass(frozen=True)
class SeveranceFinancialSnapshot:
    gross: Decimal
    personal_income_tax: Decimal
    military_levy: Decimal
    net: Decimal
    employer_contribution: Decimal

    def validate(self):
        values = {}
        for field in (
            "gross", "personal_income_tax",
            "military_levy", "net", "employer_contribution"
        ):
            values[field] = amount(
                getattr(self, field), field, nonnegative=True
            )
        if (
            values["personal_income_tax"] +
            values["military_levy"] > values["gross"]
            or values["net"] != (
                values["gross"]
                - values["personal_income_tax"]
                - values["military_levy"]
            )
        ):
            raise SeveranceSettlementError(
                "Severance-only gross, taxes and net do not reconcile"
            )
        return values


@dataclass(frozen=True)
class SeveranceFinancialPlan:
    gross_delta: Decimal
    personal_income_tax_delta: Decimal
    military_levy_delta: Decimal
    employer_contribution_delta: Decimal
    net_delta: Decimal
    employee_balance: Decimal
    employee_due: Decimal
    employee_overpayment: Decimal
    personal_income_tax_balance: Decimal
    military_levy_balance: Decimal
    employer_contribution_balance: Decimal
    employee_payment_authorized: bool = False
    employee_recovery_authorized: bool = False
    tax_refund_authorized: bool = False
    journal_posting_authorized: bool = False


def plan_severance_settlement(
    *,
    original: SeveranceFinancialSnapshot,
    corrected: SeveranceFinancialSnapshot,
    employee_paid,
    employee_returned=ZERO,
    personal_income_tax_remitted=ZERO,
    military_levy_remitted=ZERO,
    employer_contribution_remitted=ZERO,
):
    if not isinstance(original, SeveranceFinancialSnapshot):
        raise SeveranceSettlementError("Original snapshot required")
    if not isinstance(corrected, SeveranceFinancialSnapshot):
        raise SeveranceSettlementError("Corrected snapshot required")

    before = original.validate()
    after = corrected.validate()

    paid = amount(employee_paid, "employee_paid", nonnegative=True)
    returned = amount(
        employee_returned, "employee_returned", nonnegative=True
    )
    if returned > paid:
        raise SeveranceSettlementError(
            "Confirmed returns exceed previous employee payments"
        )

    pit_paid = amount(
        personal_income_tax_remitted,
        "personal_income_tax_remitted", nonnegative=True
    )
    levy_paid = amount(
        military_levy_remitted,
        "military_levy_remitted", nonnegative=True
    )
    usc_paid = amount(
        employer_contribution_remitted,
        "employer_contribution_remitted", nonnegative=True
    )

    balance = after["net"] - paid + returned

    return SeveranceFinancialPlan(
        gross_delta=after["gross"] - before["gross"],
        personal_income_tax_delta=(
            after["personal_income_tax"]
            - before["personal_income_tax"]
        ),
        military_levy_delta=(
            after["military_levy"] - before["military_levy"]
        ),
        employer_contribution_delta=(
            after["employer_contribution"]
            - before["employer_contribution"]
        ),
        net_delta=after["net"] - before["net"],
        employee_balance=balance,
        employee_due=max(ZERO, balance),
        employee_overpayment=max(ZERO, -balance),
        personal_income_tax_balance=(
            after["personal_income_tax"] - pit_paid
        ),
        military_levy_balance=(
            after["military_levy"] - levy_paid
        ),
        employer_contribution_balance=(
            after["employer_contribution"] - usc_paid
        ),
    )
