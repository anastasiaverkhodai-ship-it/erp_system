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
from app.models.payroll_sick_leave import (
    PayrollSickLeaveCalculation,
    PayrollSickLeaveCalculationSource,
)


class PayrollSickLeaveError(Exception):
    pass


class PayrollSickLeaveNotFoundError(PayrollSickLeaveError):
    pass


class PayrollSickLeaveConflictError(PayrollSickLeaveError):
    pass


class PayrollSickLeaveDerivationError(PayrollSickLeaveError):
    pass


MONEY = Decimal("0.01")
AVERAGE = Decimal("0.000001")
PERCENT = Decimal("0.0001")
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


def _percent(value: Decimal) -> Decimal:
    return Decimal(value).quantize(
        PERCENT,
        rounding=ROUND_HALF_UP,
    )


async def get_sick_leave_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    sick_leave_calculation_id: int,
) -> PayrollSickLeaveCalculation:
    row = await db.scalar(
        select(PayrollSickLeaveCalculation).where(
            PayrollSickLeaveCalculation.company_id == company_id,
            PayrollSickLeaveCalculation.id
            == sick_leave_calculation_id,
        )
    )

    if row is None:
        raise PayrollSickLeaveNotFoundError(
            "Sick leave calculation not found"
        )

    return row


async def get_sick_leave_calculation_for_leave(
    db: AsyncSession,
    *,
    company_id: int,
    leave_request_id: int,
) -> PayrollSickLeaveCalculation | None:
    return await db.scalar(
        select(PayrollSickLeaveCalculation).where(
            PayrollSickLeaveCalculation.company_id == company_id,
            PayrollSickLeaveCalculation.leave_request_id
            == leave_request_id,
        )
    )


async def list_sick_leave_calculation_sources(
    db: AsyncSession,
    *,
    company_id: int,
    sick_leave_calculation_id: int,
) -> list[PayrollSickLeaveCalculationSource]:
    result = await db.scalars(
        select(PayrollSickLeaveCalculationSource)
        .where(
            PayrollSickLeaveCalculationSource.company_id
            == company_id,
            PayrollSickLeaveCalculationSource
            .sick_leave_calculation_id
            == sick_leave_calculation_id,
        )
        .order_by(PayrollSickLeaveCalculationSource.id)
    )

    return list(result)


async def calculate_sick_leave_pay(
    db: AsyncSession,
    *,
    company_id: int,
    leave_request_id: int,
    benefit_case_code: str,
    reference_period_start: date,
    reference_period_end: date,
    eligible_earnings: Decimal,
    eligible_days: Decimal,
    insurance_service_months: int,
    benefit_percent: Decimal,
    limited_service_rule_applied: bool,
    employer_days: Decimal,
    insurer_days: Decimal,
    rule_code: str,
    rule_version: str,
    calculated_by: int,
    sources: list[dict] | None = None,
) -> PayrollSickLeaveCalculation:
    existing = await get_sick_leave_calculation_for_leave(
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
        raise PayrollSickLeaveNotFoundError(
            "Leave request not found"
        )

    if leave_request.status != LeaveRequestStatus.APPROVED:
        raise PayrollSickLeaveConflictError(
            "Sick leave pay requires approved leave"
        )

    if leave_request.leave_type != LeaveType.SICK:
        raise PayrollSickLeaveConflictError(
            "Sick leave pay requires sick leave"
        )

    if reference_period_end < reference_period_start:
        raise PayrollSickLeaveDerivationError(
            "Invalid sick leave reference period"
        )

    earnings = _money(Decimal(eligible_earnings))
    days = Decimal(eligible_days)
    service_months = int(insurance_service_months)
    percent = _percent(Decimal(benefit_percent))

    if earnings < ZERO:
        raise PayrollSickLeaveDerivationError(
            "Eligible earnings cannot be negative"
        )

    if days <= ZERO:
        raise PayrollSickLeaveDerivationError(
            "Eligible days must be positive"
        )

    if service_months < 0:
        raise PayrollSickLeaveDerivationError(
            "Insurance service months cannot be negative"
        )

    if percent <= ZERO or percent > Decimal("100"):
        raise PayrollSickLeaveDerivationError(
            "Benefit percent must be greater than 0 and at most 100"
        )

    if not benefit_case_code.strip():
        raise PayrollSickLeaveDerivationError(
            "Benefit case code is required"
        )

    if not rule_code.strip():
        raise PayrollSickLeaveDerivationError(
            "Sick leave rule code is required"
        )

    if not rule_version.strip():
        raise PayrollSickLeaveDerivationError(
            "Sick leave rule version is required"
        )

    sick_days = Decimal(
        (leave_request.end_date - leave_request.start_date).days
        + 1
    )

    if sick_days <= ZERO:
        raise PayrollSickLeaveDerivationError(
            "Sick leave days must be positive"
        )

    employer_days_value = Decimal(employer_days)
    insurer_days_value = Decimal(insurer_days)

    if employer_days_value < ZERO or insurer_days_value < ZERO:
        raise PayrollSickLeaveDerivationError(
            "Financing days cannot be negative"
        )

    if employer_days_value + insurer_days_value != sick_days:
        raise PayrollSickLeaveDerivationError(
            "Employer and insurer days must equal sick leave days"
        )

    average_daily = _average(earnings / days)

    daily_benefit = _average(
        average_daily * percent / Decimal("100")
    )

    employer_amount = _money(
        daily_benefit * employer_days_value
    )

    insurer_amount = _money(
        daily_benefit * insurer_days_value
    )

    sick_pay_amount = _money(
        employer_amount + insurer_amount
    )

    calculation = PayrollSickLeaveCalculation(
        company_id=company_id,
        leave_request_id=leave_request.id,
        employment_contract_id=(
            leave_request.employment_contract_id
        ),
        benefit_case_code=benefit_case_code.strip(),
        sick_days=sick_days,
        reference_period_start=reference_period_start,
        reference_period_end=reference_period_end,
        eligible_earnings=earnings,
        eligible_days=days,
        average_daily_amount=average_daily,
        insurance_service_months=service_months,
        benefit_percent=percent,
        limited_service_rule_applied=bool(
            limited_service_rule_applied
        ),
        daily_benefit_amount=daily_benefit,
        employer_days=employer_days_value,
        insurer_days=insurer_days_value,
        employer_amount=employer_amount,
        insurer_amount=insurer_amount,
        sick_pay_amount=sick_pay_amount,
        currency_code="UAH",
        rule_code=rule_code.strip(),
        rule_version=rule_version.strip(),
        calculated_by=calculated_by,
    )

    db.add(calculation)
    await db.flush()

    for source in sources or []:
        source_type = str(source["source_type"]).strip()

        if source_type not in {
            "earnings_history",
            "payroll_calculation",
            "insurance_service_evidence",
        }:
            raise PayrollSickLeaveDerivationError(
                "Unsupported sick leave source type"
            )

        source_id = int(source["source_id"])

        if source_id <= 0:
            raise PayrollSickLeaveDerivationError(
                "Sick leave source id must be positive"
            )

        source_earnings = _money(
            Decimal(source.get("earnings_amount", ZERO))
        )

        source_days = Decimal(
            source.get("eligible_days", ZERO)
        )

        if source_earnings < ZERO:
            raise PayrollSickLeaveDerivationError(
                "Sick leave source earnings cannot be negative"
            )

        if source_days < ZERO:
            raise PayrollSickLeaveDerivationError(
                "Sick leave source days cannot be negative"
            )

        db.add(
            PayrollSickLeaveCalculationSource(
                company_id=company_id,
                sick_leave_calculation_id=calculation.id,
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


async def derive_sick_pay_lines(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input,
    period,
) -> list[dict]:
    result = await db.scalars(
        select(PayrollSickLeaveCalculation)
        .join(
            LeaveRequest,
            (
                LeaveRequest.company_id
                == PayrollSickLeaveCalculation.company_id
            )
            & (
                LeaveRequest.id
                == PayrollSickLeaveCalculation.leave_request_id
            ),
        )
        .where(
            PayrollSickLeaveCalculation.company_id == company_id,
            PayrollSickLeaveCalculation.employment_contract_id
            == payroll_input.employment_contract_id,
            LeaveRequest.status == LeaveRequestStatus.APPROVED,
            LeaveRequest.leave_type == LeaveType.SICK,
            LeaveRequest.start_date <= period.end_date,
            LeaveRequest.end_date >= period.start_date,
        )
        .order_by(PayrollSickLeaveCalculation.id)
    )

    rows = list(result)

    lines: list[dict] = []

    for row in rows:
        lines.append(
            {
                "line_type": "sick_pay",
                "description": (
                    f"Sick leave {row.benefit_case_code}"
                ),
                "quantity": row.sick_days,
                "rate": row.daily_benefit_amount,
                "amount": row.sick_pay_amount,
                "currency_code": row.currency_code,
                "salary_rate_type": None,
                "source_salary_rate_id": None,
                "source_effective_from": None,
                "source_effective_to": None,
                "source_supplement_id": None,
                "source_vacation_calculation_id": None,
                "source_sick_leave_calculation_id": row.id,
            }
        )

    return lines
