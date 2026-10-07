from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leave_request import (
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
)
from app.models.payroll_vacation import (
    PayrollVacationCalculation,
    PayrollVacationCalculationSource,
)


class PayrollVacationError(Exception):
    pass


class PayrollVacationNotFoundError(PayrollVacationError):
    pass


class PayrollVacationConflictError(PayrollVacationError):
    pass


class PayrollVacationDerivationError(PayrollVacationError):
    pass


MONEY = Decimal("0.01")
AVERAGE = Decimal("0.000001")
ZERO = Decimal("0.00")


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(
        MONEY,
        rounding=ROUND_HALF_UP,
    )


def _average(value: Decimal) -> Decimal:
    return Decimal(value).quantize(
        AVERAGE,
        rounding=ROUND_HALF_UP,
    )


async def get_vacation_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    vacation_calculation_id: int,
) -> PayrollVacationCalculation:
    row = await db.scalar(
        select(PayrollVacationCalculation).where(
            PayrollVacationCalculation.company_id
            == company_id,
            PayrollVacationCalculation.id
            == vacation_calculation_id,
        )
    )

    if row is None:
        raise PayrollVacationNotFoundError(
            "Vacation calculation not found"
        )

    return row


async def get_vacation_calculation_for_leave(
    db: AsyncSession,
    *,
    company_id: int,
    leave_request_id: int,
) -> PayrollVacationCalculation | None:
    return await db.scalar(
        select(PayrollVacationCalculation).where(
            PayrollVacationCalculation.company_id
            == company_id,
            PayrollVacationCalculation.leave_request_id
            == leave_request_id,
        )
    )


async def list_vacation_calculation_sources(
    db: AsyncSession,
    *,
    company_id: int,
    vacation_calculation_id: int,
) -> list[PayrollVacationCalculationSource]:
    result = await db.scalars(
        select(PayrollVacationCalculationSource)
        .where(
            PayrollVacationCalculationSource.company_id
            == company_id,
            PayrollVacationCalculationSource
            .vacation_calculation_id
            == vacation_calculation_id,
        )
        .order_by(
            PayrollVacationCalculationSource.id
        )
    )

    return list(result)


async def calculate_vacation_pay(
    db: AsyncSession,
    *,
    company_id: int,
    leave_request_id: int,
    reference_period_start: date,
    reference_period_end: date,
    eligible_earnings: Decimal,
    eligible_days: Decimal,
    rule_code: str,
    rule_version: str,
    calculated_by: int,
    sources: list[dict] | None = None,
) -> PayrollVacationCalculation:
    existing = await get_vacation_calculation_for_leave(
        db,
        company_id=company_id,
        leave_request_id=leave_request_id,
    )

    if existing is not None:
        return existing

    leave_request = await db.scalar(
        select(LeaveRequest).where(
            LeaveRequest.company_id == company_id,
            LeaveRequest.id == leave_request_id,
        )
    )

    if leave_request is None:
        raise PayrollVacationNotFoundError(
            "Leave request not found"
        )

    if leave_request.status != LeaveRequestStatus.APPROVED:
        raise PayrollVacationConflictError(
            "Vacation pay requires approved leave"
        )

    if leave_request.leave_type != LeaveType.ANNUAL:
        raise PayrollVacationConflictError(
            "Vacation pay requires annual leave"
        )

    if reference_period_end < reference_period_start:
        raise PayrollVacationDerivationError(
            "Invalid vacation reference period"
        )

    earnings = _money(Decimal(eligible_earnings))
    days = Decimal(eligible_days)

    if earnings < ZERO:
        raise PayrollVacationDerivationError(
            "Eligible earnings cannot be negative"
        )

    if days <= ZERO:
        raise PayrollVacationDerivationError(
            "Eligible days must be positive"
        )

    if not rule_code.strip():
        raise PayrollVacationDerivationError(
            "Vacation rule code is required"
        )

    if not rule_version.strip():
        raise PayrollVacationDerivationError(
            "Vacation rule version is required"
        )

    leave_days = Decimal(
        (
            leave_request.end_date
            - leave_request.start_date
        ).days
        + 1
    )

    if leave_days <= ZERO:
        raise PayrollVacationDerivationError(
            "Vacation leave days must be positive"
        )

    average_daily = _average(
        earnings / days
    )

    vacation_pay = _money(
        average_daily * leave_days
    )

    calculation = PayrollVacationCalculation(
        company_id=company_id,
        leave_request_id=leave_request.id,
        employment_contract_id=(
            leave_request.employment_contract_id
        ),
        leave_days=leave_days,
        reference_period_start=reference_period_start,
        reference_period_end=reference_period_end,
        eligible_earnings=earnings,
        eligible_days=days,
        average_daily_amount=average_daily,
        vacation_pay_amount=vacation_pay,
        currency_code="UAH",
        rule_code=rule_code.strip(),
        rule_version=rule_version.strip(),
        calculated_by=calculated_by,
    )

    db.add(calculation)
    await db.flush()

    for source in sources or []:
        source_type = str(
            source["source_type"]
        ).strip()

        if source_type not in {
            "earnings_history",
            "payroll_calculation",
            "leave_opening",
        }:
            raise PayrollVacationDerivationError(
                "Unsupported vacation source type"
            )

        source_id = int(source["source_id"])

        if source_id <= 0:
            raise PayrollVacationDerivationError(
                "Vacation source id must be positive"
            )

        source_earnings = _money(
            Decimal(
                source.get(
                    "earnings_amount",
                    ZERO,
                )
            )
        )

        source_days = Decimal(
            source.get(
                "eligible_days",
                ZERO,
            )
        )

        if source_earnings < ZERO:
            raise PayrollVacationDerivationError(
                "Vacation source earnings "
                "cannot be negative"
            )

        if source_days < ZERO:
            raise PayrollVacationDerivationError(
                "Vacation source days "
                "cannot be negative"
            )

        db.add(
            PayrollVacationCalculationSource(
                company_id=company_id,
                vacation_calculation_id=calculation.id,
                source_type=source_type,
                source_id=source_id,
                source_period_start=source.get(
                    "source_period_start"
                ),
                source_period_end=source.get(
                    "source_period_end"
                ),
                earnings_amount=source_earnings,
                eligible_days=source_days,
                source_reference=source.get(
                    "source_reference"
                ),
            )
        )

    await db.flush()

    return calculation


async def derive_vacation_pay_lines(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input,
    period,
) -> list[dict]:
    result = await db.execute(
        select(PayrollVacationCalculation, LeaveRequest)
        .join(
            LeaveRequest,
            (
                LeaveRequest.company_id
                == PayrollVacationCalculation.company_id
            )
            & (
                LeaveRequest.id
                == PayrollVacationCalculation.leave_request_id
            ),
        )
        .where(
            PayrollVacationCalculation.company_id
            == company_id,
            PayrollVacationCalculation.employment_contract_id
            == payroll_input.employment_contract_id,
            LeaveRequest.status
            == LeaveRequestStatus.APPROVED,
            LeaveRequest.leave_type
            == LeaveType.ANNUAL,
            LeaveRequest.start_date
            <= period.end_date,
            LeaveRequest.end_date
            >= period.start_date,
        )
        .order_by(
            PayrollVacationCalculation.id
        )
    )

    rows = list(result)

    lines: list[dict] = []

    from app.services.payroll_leave_allocation import allocate_leave_amount
    for row, leave in rows:
        if Decimal((leave.end_date - leave.start_date).days + 1) != row.leave_days:
            raise PayrollVacationDerivationError('Leave dates changed after calculation; use a correction')
        allocated_days, allocated_amount = allocate_leave_amount(
            start=leave.start_date, end=leave.end_date,
            period_start=period.start_date, period_end=period.end_date,
            amount=row.vacation_pay_amount)
        lines.append(
            {
                "line_type": "vacation_pay",
                "quantity": Decimal(allocated_days),
                "rate": row.average_daily_amount,
                "amount": allocated_amount,
                "currency_code": row.currency_code,
                "source_salary_rate_id": None,
                "source_supplement_id": None,
                "source_vacation_calculation_id": row.id,
            }
        )

    return lines
