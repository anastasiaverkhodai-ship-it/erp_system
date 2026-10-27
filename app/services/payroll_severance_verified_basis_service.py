"""Read-only preparation of documented severance source evidence.

No insertion, correction, payroll posting or implicit approval.
The caller must separately enforce idempotency and revision chains.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
)
from app.services.payroll_severance_database_average import (
    derive_database_severance_average,
)
from app.services.payroll_termination_severance_policy import (
    calculate_statutory_severance_minimum,
)
from app.services.payroll_termination_severance_preflight import (
    preflight_termination_severance,
)


@dataclass(frozen=True)
class VerifiedSeveranceBasis:
    company_id: int
    employment_contract_id: int
    termination_event_id: int
    termination_date: date
    order_number: str
    policy_category: str
    multiplier: int
    basis_as_of: date
    basis_reference: str
    base_amount: Decimal
    gross_amount: Decimal
    currency_code: str


async def prepare_verified_severance_basis(
    db,
    *,
    company_id: int,
    employment_contract_id: int,
    payroll_input_id: int,
):
    """Prepare a proven basis; never trust a caller-supplied amount."""
    for label, value in (
        ("company_id", company_id),
        ("employment_contract_id", employment_contract_id),
        ("payroll_input_id", payroll_input_id),
    ):
        if type(value) is not int or value <= 0:
            raise SeveranceAverageError(
                f"Invalid {label}"
            )

    preflight = await preflight_termination_severance(
        db,
        company_id=company_id,
        payroll_input_id=payroll_input_id,
    )

    if preflight.company_id != company_id:
        raise SeveranceAverageError(
            "Severance preflight company mismatch"
        )

    policy = preflight.policy

    if policy.category != "average_months":
        raise SeveranceAverageError(
            "This verified average source does not support "
            "minimum-wage or manual-review severance"
        )

    if (
        type(policy.multiplier) is not int
        or policy.multiplier <= 0
    ):
        raise SeveranceAverageError(
            "Invalid severance multiplier"
        )

    termination_date = preflight.termination_date

    if type(termination_date) is not date:
        raise SeveranceAverageError(
            "Documented termination date required"
        )

    order = preflight.termination_order_number
    if not isinstance(order, str) or not order.strip():
        raise SeveranceAverageError(
            "Documented termination order required"
        )

    if (
        type(preflight.termination_event_id) is not int
        or preflight.termination_event_id <= 0
    ):
        raise SeveranceAverageError(
            "Valid termination event required"
        )

    average = await derive_database_severance_average(
        db,
        company_id=company_id,
        employment_contract_id=employment_contract_id,
        termination_date=termination_date,
    )

    base = average.average_monthly

    if (
        not isinstance(base, Decimal)
        or not base.is_finite()
        or base <= 0
    ):
        raise SeveranceAverageError(
            "Invalid database-derived average"
        )

    gross = calculate_statutory_severance_minimum(
        policy,
        average_monthly_earnings=base,
    )

    if not gross.is_finite() or gross <= 0:
        raise SeveranceAverageError(
            "Invalid severance minimum"
        )

    reference = (
        "UA_ORDER_100_TWO_MONTHS; "
        f"{average.reference_start.isoformat()}.."
        f"{average.reference_end.isoformat()}; "
        f"{average.source_reference}"
    )

    if len(reference) > 1000:
        raise SeveranceAverageError(
            "Severance source reference exceeds DB limit; "
            "requires a durable separate evidence snapshot"
        )

    return VerifiedSeveranceBasis(
        company_id=company_id,
        employment_contract_id=employment_contract_id,
        termination_event_id=preflight.termination_event_id,
        termination_date=termination_date,
        order_number=order.strip(),
        policy_category=policy.category,
        multiplier=policy.multiplier,
        basis_as_of=termination_date,
        basis_reference=reference,
        base_amount=base,
        gross_amount=gross,
        currency_code="UAH",
    )
