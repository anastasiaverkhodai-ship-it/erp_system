from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import PayrollCalculation
from app.models.payroll import PayrollPeriod
from app.models.payroll_statutory import (
    PayrollStatutoryComponent,
    PayrollStatutoryRate,
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)


MONEY_QUANTUM = Decimal("0.01")


class PayrollStatutoryError(ValueError):
    pass


class PayrollStatutoryNotFoundError(
    PayrollStatutoryError
):
    pass


class PayrollStatutoryConflictError(
    PayrollStatutoryError
):
    pass


class PayrollStatutoryValidationError(
    PayrollStatutoryError
):
    pass


def round_money(value: Decimal) -> Decimal:
    return value.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def validate_rate_window(
    *,
    effective_from: date,
    effective_to: date | None,
) -> None:
    if (
        effective_to is not None
        and effective_to < effective_from
    ):
        raise PayrollStatutoryValidationError(
            "effective_to must be on or after "
            "effective_from"
        )


async def create_statutory_rate(
    session: AsyncSession,
    *,
    company_id: int,
    component: PayrollStatutoryComponent,
    rate: Decimal,
    effective_from: date,
    effective_to: date | None,
    actor_user_id: int,
) -> PayrollStatutoryRate:
    validate_rate_window(
        effective_from=effective_from,
        effective_to=effective_to,
    )

    if rate < Decimal("0") or rate > Decimal("1"):
        raise PayrollStatutoryValidationError(
            "statutory rate must be between 0 and 1"
        )

    overlap_stmt = (
        select(PayrollStatutoryRate.id)
        .where(
            PayrollStatutoryRate.company_id
            == company_id,
            PayrollStatutoryRate.component
            == component.value,
            PayrollStatutoryRate.effective_from
            <= (
                effective_to
                if effective_to is not None
                else date.max
            ),
            or_(
                PayrollStatutoryRate.effective_to
                .is_(None),
                PayrollStatutoryRate.effective_to
                >= effective_from,
            ),
        )
        .limit(1)
    )

    existing_id = (
        await session.execute(overlap_stmt)
    ).scalar_one_or_none()

    if existing_id is not None:
        raise PayrollStatutoryConflictError(
            "statutory rate effective window overlaps "
            "an existing rate"
        )

    obj = PayrollStatutoryRate(
        company_id=company_id,
        component=component.value,
        rate=rate,
        effective_from=effective_from,
        effective_to=effective_to,
        created_by=actor_user_id,
    )

    session.add(obj)
    await session.flush()
    await session.refresh(obj)

    return obj


async def list_statutory_rates(
    session: AsyncSession,
    *,
    company_id: int,
    component: PayrollStatutoryComponent | None = None,
) -> list[PayrollStatutoryRate]:
    stmt = (
        select(PayrollStatutoryRate)
        .where(
            PayrollStatutoryRate.company_id
            == company_id
        )
        .order_by(
            PayrollStatutoryRate.component.asc(),
            PayrollStatutoryRate.effective_from.asc(),
            PayrollStatutoryRate.id.asc(),
        )
    )

    if component is not None:
        stmt = stmt.where(
            PayrollStatutoryRate.component
            == component.value
        )

    return list(
        (
            await session.execute(stmt)
        ).scalars().all()
    )


async def resolve_statutory_rate(
    session: AsyncSession,
    *,
    company_id: int,
    component: PayrollStatutoryComponent,
    effective_date: date,
) -> PayrollStatutoryRate:
    stmt = (
        select(PayrollStatutoryRate)
        .where(
            PayrollStatutoryRate.company_id
            == company_id,
            PayrollStatutoryRate.component
            == component.value,
            PayrollStatutoryRate.effective_from
            <= effective_date,
            or_(
                PayrollStatutoryRate.effective_to
                .is_(None),
                PayrollStatutoryRate.effective_to
                >= effective_date,
            ),
        )
        .order_by(
            PayrollStatutoryRate.effective_from.desc(),
            PayrollStatutoryRate.id.desc(),
        )
        .limit(1)
    )

    obj = (
        await session.execute(stmt)
    ).scalar_one_or_none()

    if obj is None:
        raise PayrollStatutoryNotFoundError(
            "no statutory rate applies on "
            f"{effective_date.isoformat()}"
        )

    return obj


async def get_statutory_result(
    session: AsyncSession,
    *,
    company_id: int,
    statutory_result_id: int,
) -> PayrollStatutoryResult:
    stmt = select(
        PayrollStatutoryResult
    ).where(
        PayrollStatutoryResult.company_id
        == company_id,
        PayrollStatutoryResult.id
        == statutory_result_id,
    )

    obj = (
        await session.execute(stmt)
    ).scalar_one_or_none()

    if obj is None:
        raise PayrollStatutoryNotFoundError(
            "payroll statutory result not found"
        )

    return obj


async def get_statutory_result_for_calculation(
    session: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> PayrollStatutoryResult | None:
    stmt = select(
        PayrollStatutoryResult
    ).where(
        PayrollStatutoryResult.company_id
        == company_id,
        PayrollStatutoryResult.payroll_calculation_id
        == payroll_calculation_id,
    )

    return (
        await session.execute(stmt)
    ).scalar_one_or_none()


async def list_statutory_result_lines(
    session: AsyncSession,
    *,
    company_id: int,
    statutory_result_id: int,
) -> list[PayrollStatutoryResultLine]:
    stmt = (
        select(PayrollStatutoryResultLine)
        .where(
            PayrollStatutoryResultLine.company_id
            == company_id,
            PayrollStatutoryResultLine
            .payroll_statutory_result_id
            == statutory_result_id,
        )
        .order_by(
            PayrollStatutoryResultLine.line_no.asc()
        )
    )

    return list(
        (
            await session.execute(stmt)
        ).scalars().all()
    )


async def get_payroll_calculation(
    session: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> PayrollCalculation:
    stmt = select(
        PayrollCalculation
    ).where(
        PayrollCalculation.company_id == company_id,
        PayrollCalculation.id
        == payroll_calculation_id,
    )

    obj = (
        await session.execute(stmt)
    ).scalar_one_or_none()

    if obj is None:
        raise PayrollStatutoryNotFoundError(
            "payroll calculation not found"
        )

    return obj


def calculate_component_amount(
    *,
    base_amount: Decimal,
    rate: Decimal,
) -> Decimal:
    if base_amount < Decimal("0"):
        raise PayrollStatutoryValidationError(
            "statutory base amount cannot be negative"
        )

    if rate < Decimal("0") or rate > Decimal("1"):
        raise PayrollStatutoryValidationError(
            "statutory rate must be between 0 and 1"
        )

    return round_money(base_amount * rate)


async def calculate_payroll_statutory_result(
    session: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
    actor_user_id: int,
) -> PayrollStatutoryResult:
    existing = await get_statutory_result_for_calculation(
        session,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    if existing is not None:
        return existing

    calculation = await get_payroll_calculation(
        session,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    payroll_period = await session.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.id
            == calculation.payroll_period_id,
            PayrollPeriod.company_id
            == company_id,
        )
    )
    if payroll_period is None:
        raise PayrollStatutoryNotFoundError(
            "Payroll period not found"
        )

    effective_date = payroll_period.end_date

    gross_amount = round_money(
        Decimal(calculation.gross_amount)
    )

    if gross_amount < Decimal("0"):
        raise PayrollStatutoryValidationError(
            "payroll gross amount cannot be negative"
        )

    currency_code = str(
        calculation.currency_code
    ).upper()

    if (
        len(currency_code) != 3
        or not currency_code.isalpha()
    ):
        raise PayrollStatutoryValidationError(
            "payroll calculation currency must be "
            "a 3-letter alphabetic code"
        )

    components = (
        PayrollStatutoryComponent.PERSONAL_INCOME_TAX,
        PayrollStatutoryComponent.MILITARY_LEVY,
        PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION,
    )

    rates: list[PayrollStatutoryRate] = []

    for component in components:
        rate_obj = await resolve_statutory_rate(
            session,
            company_id=company_id,
            component=component,
            effective_date=effective_date,
        )
        rates.append(rate_obj)

    line_payloads: list[
        tuple[
            PayrollStatutoryComponent,
            PayrollStatutoryRate,
            Decimal,
        ]
    ] = []

    for component, rate_obj in zip(
        components,
        rates,
        strict=True,
    ):
        amount = calculate_component_amount(
            base_amount=gross_amount,
            rate=Decimal(rate_obj.rate),
        )

        line_payloads.append(
            (
                component,
                rate_obj,
                amount,
            )
        )

    employee_withholding_amount = sum(
        (
            amount
            for component, _, amount in line_payloads
            if component
            in {
                PayrollStatutoryComponent.PERSONAL_INCOME_TAX,
                PayrollStatutoryComponent.MILITARY_LEVY,
            }
        ),
        Decimal("0.00"),
    )

    employer_contribution_amount = sum(
        (
            amount
            for component, _, amount in line_payloads
            if component
            == PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION
        ),
        Decimal("0.00"),
    )

    employee_withholding_amount = round_money(
        employee_withholding_amount
    )

    employer_contribution_amount = round_money(
        employer_contribution_amount
    )

    net_amount = round_money(
        gross_amount - employee_withholding_amount
    )

    if net_amount < Decimal("0"):
        raise PayrollStatutoryValidationError(
            "employee statutory withholdings exceed "
            "gross payroll amount"
        )

    result = PayrollStatutoryResult(
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
        currency_code=currency_code,
        gross_amount=gross_amount,
        employee_withholding_amount=(
            employee_withholding_amount
        ),
        employer_contribution_amount=(
            employer_contribution_amount
        ),
        net_amount=net_amount,
        calculated_by=actor_user_id,
    )

    session.add(result)
    await session.flush()

    for line_no, payload in enumerate(
        line_payloads,
        start=1,
    ):
        component, rate_obj, amount = payload

        line = PayrollStatutoryResultLine(
            company_id=company_id,
            payroll_statutory_result_id=result.id,
            line_no=line_no,
            component=component.value,
            base_amount=gross_amount,
            rate=Decimal(rate_obj.rate),
            amount=amount,
            currency_code=currency_code,
            source_rate_id=rate_obj.id,
            rate_effective_from=(
                rate_obj.effective_from
            ),
            rate_effective_to=(
                rate_obj.effective_to
            ),
        )

        session.add(line)

    await session.flush()
    await session.refresh(result)

    return result
