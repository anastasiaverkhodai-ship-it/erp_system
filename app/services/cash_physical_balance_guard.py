"""Strict caller-owned physical cash chronology guard.

Requires an independently reviewed opening and GL-verified
cash events. Uses the same cash desk row as serialization lock.

No commit, no rollback and no accounting mutation occurs here.
This does NOT automatically protect direct journal writes.
All writers must eventually call it inside their own transaction.
"""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cash_physical_opening_adapter import (
    load_reviewed_cash_opening,
    CashOpeningUnavailableError,
)
from app.services.cash_physical_event_reader import (
    read_physical_cash_events,
    CashEventSourceError,
)
from app.services.cash_physical_chronology_service import (
    calculate_cash_chronology,
)


class PhysicalCashBalanceGuardError(ValueError):
    pass


async def validate_locked_physical_cash(
    db: AsyncSession,
    *,
    company_id: int,
    cash_desk_id: int,
    as_of_date: date,
):
    """Validate historical physical cash under a desk row lock.

    The lock remains owned by the caller's database transaction.
    This function must execute before that transaction commits.
    """

    if (
        type(company_id) is not int
        or type(cash_desk_id) is not int
        or company_id <= 0
        or cash_desk_id <= 0
        or type(as_of_date) is not date
    ):
        raise PhysicalCashBalanceGuardError(
            "Invalid cash scope or evaluation date"
        )

    try:
        opening = await load_reviewed_cash_opening(
            db,
            company_id=company_id,
            cash_desk_id=cash_desk_id,
            as_of_date=as_of_date,
            lock_cash_desk=True,
        )

        movements = await read_physical_cash_events(
            db,
            company_id=company_id,
            cash_desk_id=cash_desk_id,
            currency_code=opening.currency_code,
            effective_date=opening.effective_date,
        )

        result = calculate_cash_chronology(
            opening=opening,
            movements=movements,
        )

    except (
        CashOpeningUnavailableError,
        CashEventSourceError,
        ValueError,
    ) as exc:
        raise PhysicalCashBalanceGuardError(
            "Physical cash chronology cannot be certified: "
            + str(exc)
        ) from exc

    return result
