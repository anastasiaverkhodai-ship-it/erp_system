"""Phase 13 C4-A — technical remittance planning rules.

This module models candidate remittance allocations only.

It DOES NOT:
- transfer money;
- authorize deductions;
- authorize remittances;
- generate payment instructions;
- modify payroll;
- create accounting entries;
- write to a database;
- commit or rollback transactions.

The monetary ceiling is a technical input supplied by a
trusted caller. It is NOT proof of legal entitlement or of
actual funds available for transfer.

Legal authorization remains NOT_AUTHORIZED.
"""

from dataclasses import dataclass
from decimal import Decimal


CENT = Decimal("0.01")


class RemittanceRuleError(ValueError):
    """Technical remittance rule violation."""


def _positive_id(value, name):
    if type(value) is not int or value <= 0:
        raise RemittanceRuleError(
            f"{name} must be a positive integer"
        )


def _money(value, name, *, allow_zero=True):
    if type(value) is not Decimal:
        raise RemittanceRuleError(
            f"{name} must be Decimal"
        )

    if not value.is_finite():
        raise RemittanceRuleError(
            f"{name} must be finite"
        )

    if value < 0 or (value == 0 and not allow_zero):
        raise RemittanceRuleError(
            f"{name} must be positive"
            if not allow_zero
            else f"{name} cannot be negative"
        )

    if value != value.quantize(CENT):
        raise RemittanceRuleError(
            f"{name} must have at most two decimal places"
        )

    return value


def _currency(value):
    if (
        type(value) is not str
        or len(value) != 3
        or not value.isascii()
        or not value.isalpha()
        or value != value.upper()
    ):
        raise RemittanceRuleError(
            "currency_code must contain three uppercase ASCII letters"
        )


def _reference(value, name, *, maximum=120):
    if (
        type(value) is not str
        or not value
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise RemittanceRuleError(
            f"Invalid {name}"
        )


@dataclass(frozen=True)
class RemittanceSource:
    """Immutable technical limit and beneficiary binding.

    The source is not a legal authorization.
    """

    company_id: int
    instruction_id: int
    beneficiary_reference: str
    currency_code: str
    available_amount: Decimal

    def __post_init__(self):
        _positive_id(self.company_id, "company_id")
        _positive_id(self.instruction_id, "instruction_id")
        _reference(
            self.beneficiary_reference,
            "beneficiary_reference",
        )
        _currency(self.currency_code)
        _money(
            self.available_amount,
            "available_amount",
        )


@dataclass(frozen=True)
class RemittanceEvent:
    """Technical candidate allocation, not a payment."""

    event_id: str
    company_id: int
    instruction_id: int
    beneficiary_reference: str
    currency_code: str
    amount: Decimal

    def __post_init__(self):
        _reference(self.event_id, "event_id", maximum=100)
        _positive_id(self.company_id, "company_id")
        _positive_id(self.instruction_id, "instruction_id")
        _reference(
            self.beneficiary_reference,
            "beneficiary_reference",
        )
        _currency(self.currency_code)
        _money(
            self.amount,
            "amount",
            allow_zero=False,
        )


@dataclass(frozen=True)
class RemittancePlan:
    source: RemittanceSource
    events: tuple = ()

    def __post_init__(self):
        if not isinstance(self.source, RemittanceSource):
            raise RemittanceRuleError(
                "Invalid remittance source"
            )

        if type(self.events) is not tuple:
            raise RemittanceRuleError(
                "Remittance events must be a tuple"
            )

        # Validate even when a plan is reconstructed manually.
        reconcile_remittance_plan(self)


@dataclass(frozen=True)
class RemittanceBalance:
    company_id: int
    instruction_id: int
    beneficiary_reference: str
    currency_code: str
    available_amount: Decimal
    allocated_amount: Decimal
    remaining_amount: Decimal
    event_count: int

    reconciled: bool = True

    payment_execution_allowed: bool = False
    payroll_posting_allowed: bool = False
    legal_authorization: str = "NOT_AUTHORIZED"


@dataclass(frozen=True)
class RemittanceApplication:
    plan: RemittancePlan
    balance: RemittanceBalance
    applied_now: Decimal
    duplicate: bool

    payment_execution_allowed: bool = False
    legal_authorization: str = "NOT_AUTHORIZED"


def reconcile_remittance_plan(
    plan: RemittancePlan,
) -> RemittanceBalance:
    """Rebuild the technical balance from all candidate events.

    Rejects foreign companies, foreign instructions, changes
    of beneficiary or currency, duplicates and over-allocation.
    """

    if not isinstance(plan, RemittancePlan):
        raise RemittanceRuleError(
            "Invalid remittance plan"
        )

    source = plan.source

    if not isinstance(source, RemittanceSource):
        raise RemittanceRuleError(
            "Invalid remittance source"
        )

    if type(plan.events) is not tuple:
        raise RemittanceRuleError(
            "Remittance events must be a tuple"
        )

    seen = set()
    allocated = Decimal("0.00")

    for event in plan.events:
        if not isinstance(event, RemittanceEvent):
            raise RemittanceRuleError(
                "Invalid remittance event"
            )

        if event.company_id != source.company_id:
            raise RemittanceRuleError(
                "Cross-company remittance event"
            )

        if event.instruction_id != source.instruction_id:
            raise RemittanceRuleError(
                "Instruction mismatch"
            )

        if (
            event.beneficiary_reference
            != source.beneficiary_reference
        ):
            raise RemittanceRuleError(
                "Beneficiary mismatch"
            )

        if event.currency_code != source.currency_code:
            raise RemittanceRuleError(
                "Currency mismatch"
            )

        if event.event_id in seen:
            raise RemittanceRuleError(
                "Duplicate remittance event"
            )

        seen.add(event.event_id)
        allocated += event.amount

        if allocated > source.available_amount:
            raise RemittanceRuleError(
                "Remittance allocation exceeds available amount"
            )

    remaining = source.available_amount - allocated

    if allocated + remaining != source.available_amount:
        raise RemittanceRuleError(
            "Remittance balance conservation mismatch"
        )

    return RemittanceBalance(
        company_id=source.company_id,
        instruction_id=source.instruction_id,
        beneficiary_reference=source.beneficiary_reference,
        currency_code=source.currency_code,
        available_amount=source.available_amount,
        allocated_amount=allocated,
        remaining_amount=remaining,
        event_count=len(plan.events),
    )


def open_remittance_plan(
    *,
    company_id: int,
    instruction_id: int,
    beneficiary_reference: str,
    currency_code: str,
    available_amount: Decimal,
) -> RemittancePlan:
    """Create an empty technical plan without executing payments."""

    return RemittancePlan(
        source=RemittanceSource(
            company_id=company_id,
            instruction_id=instruction_id,
            beneficiary_reference=beneficiary_reference,
            currency_code=currency_code,
            available_amount=available_amount,
        ),
    )


def append_remittance_event(
    *,
    plan: RemittancePlan,
    event: RemittanceEvent,
) -> RemittanceApplication:
    """Strict idempotent append of a candidate allocation.

    Identical event replay is a no-op.
    Changed payload replay is rejected.
    """

    current = reconcile_remittance_plan(plan)

    if not isinstance(event, RemittanceEvent):
        raise RemittanceRuleError(
            "Invalid remittance event"
        )

    source = plan.source

    if event.company_id != source.company_id:
        raise RemittanceRuleError(
            "Cross-company remittance event"
        )

    if event.instruction_id != source.instruction_id:
        raise RemittanceRuleError(
            "Instruction mismatch"
        )

    if event.beneficiary_reference != source.beneficiary_reference:
        raise RemittanceRuleError(
            "Beneficiary mismatch"
        )

    if event.currency_code != source.currency_code:
        raise RemittanceRuleError(
            "Currency mismatch"
        )

    for existing in plan.events:
        if existing.event_id != event.event_id:
            continue

        if existing != event:
            raise RemittanceRuleError(
                "Event ID replay with different payload"
            )

        return RemittanceApplication(
            plan=plan,
            balance=current,
            applied_now=Decimal("0.00"),
            duplicate=True,
        )

    if event.amount > current.remaining_amount:
        raise RemittanceRuleError(
            "Remittance allocation exceeds available amount"
        )

    updated = RemittancePlan(
        source=source,
        events=plan.events + (event,),
    )

    balance = reconcile_remittance_plan(updated)

    return RemittanceApplication(
        plan=updated,
        balance=balance,
        applied_now=event.amount,
        duplicate=False,
    )
