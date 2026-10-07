from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.services.payroll_mutation_guard import serialized_payroll_mutation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInput,
    PayrollInputSalarySlice,
    PayrollPeriod,
    PayrollPeriodStatus,
)


MONEY_QUANTUM = Decimal("0.01")
QUANTITY_QUANTUM = Decimal("0.0001")


class PayrollCalculationError(Exception):
    pass


class PayrollCalculationNotFoundError(PayrollCalculationError):
    pass


class PayrollCalculationConflictError(PayrollCalculationError):
    pass


class PayrollCalculationLifecycleError(PayrollCalculationError):
    pass


class PayrollCalculationDerivationError(PayrollCalculationError):
    pass


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _quantity(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(
        QUANTITY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


async def _require_period(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
) -> PayrollPeriod:
    result = await db.execute(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == payroll_period_id,
        )
    )
    period = result.scalar_one_or_none()

    if period is None:
        raise PayrollCalculationNotFoundError(
            "Payroll period not found."
        )

    return period


async def _require_input(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
) -> PayrollInput:
    result = await db.execute(
        select(PayrollInput).where(
            PayrollInput.company_id == company_id,
            PayrollInput.id == payroll_input_id,
        )
    )
    payroll_input = result.scalar_one_or_none()

    if payroll_input is None:
        raise PayrollCalculationNotFoundError(
            "Payroll input not found."
        )

    return payroll_input


async def _require_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> PayrollCalculation:
    result = await db.execute(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
        )
    )
    calculation = result.scalar_one_or_none()

    if calculation is None:
        raise PayrollCalculationNotFoundError(
            "Payroll calculation not found."
        )

    return calculation


async def get_payroll_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> PayrollCalculation:
    return await _require_calculation(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )


async def get_payroll_calculation_for_input(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
) -> PayrollCalculation | None:
    result = await db.execute(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.payroll_input_id == payroll_input_id,
        )
    )
    return result.scalar_one_or_none()


async def list_payroll_calculations(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
) -> list[PayrollCalculation]:
    result = await db.execute(
        select(PayrollCalculation)
        .where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.payroll_period_id == payroll_period_id,
        )
        .order_by(
            PayrollCalculation.employment_contract_id,
            PayrollCalculation.id,
        )
    )
    return list(result.scalars().all())


async def list_payroll_calculation_lines(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> list[PayrollCalculationLine]:
    await _require_calculation(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    result = await db.execute(
        select(PayrollCalculationLine)
        .where(
            PayrollCalculationLine.company_id == company_id,
            PayrollCalculationLine.payroll_calculation_id
            == payroll_calculation_id,
        )
        .order_by(PayrollCalculationLine.line_no)
    )
    return list(result.scalars().all())


async def load_salary_slices_for_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
) -> list[PayrollInputSalarySlice]:
    result = await db.execute(
        select(PayrollInputSalarySlice)
        .where(
            PayrollInputSalarySlice.company_id == company_id,
            PayrollInputSalarySlice.payroll_input_id
            == payroll_input_id,
        )
        .order_by(
            PayrollInputSalarySlice.effective_from,
            PayrollInputSalarySlice.id,
        )
    )
    return list(result.scalars().all())


async def validate_calculation_source(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
) -> tuple[
    PayrollPeriod,
    PayrollInput,
    list[PayrollInputSalarySlice],
]:
    payroll_input = await _require_input(
        db,
        company_id=company_id,
        payroll_input_id=payroll_input_id,
    )

    period = await _require_period(
        db,
        company_id=company_id,
        payroll_period_id=payroll_input.payroll_period_id,
    )

    if _enum_value(period.status) != PayrollPeriodStatus.FINALIZED.value:
        raise PayrollCalculationLifecycleError(
            "Payroll calculation requires a finalized payroll period."
        )

    if payroll_input.company_id != company_id:
        raise PayrollCalculationConflictError(
            "Payroll input company mismatch."
        )

    if payroll_input.payroll_period_id != period.id:
        raise PayrollCalculationConflictError(
            "Payroll input period mismatch."
        )

    salary_slices = await load_salary_slices_for_calculation(
        db,
        company_id=company_id,
        payroll_input_id=payroll_input.id,
    )

    if not salary_slices:
        raise PayrollCalculationDerivationError(
            "Payroll input has no salary slices."
        )

    currencies = {
        salary_slice.currency_code
        for salary_slice in salary_slices
    }

    if len(currencies) != 1:
        raise PayrollCalculationDerivationError(
            "Payroll input salary slices must use one currency."
        )

    currency_code = next(iter(currencies))

    if (
        len(currency_code) != 3
        or currency_code != currency_code.upper()
    ):
        raise PayrollCalculationDerivationError(
            "Payroll calculation currency must be a 3-letter uppercase code."
        )

    return period, payroll_input, salary_slices


async def ensure_calculation_absent(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
) -> None:
    existing = await get_payroll_calculation_for_input(
        db,
        company_id=company_id,
        payroll_input_id=payroll_input_id,
    )

    if existing is not None:
        raise PayrollCalculationConflictError(
            "Payroll calculation already exists for this payroll input."
        )


def _slice_days(salary_slice: PayrollInputSalarySlice) -> int:
    days = (
        salary_slice.effective_to
        - salary_slice.effective_from
    ).days + 1

    if days <= 0:
        raise PayrollCalculationDerivationError(
            "Salary slice date range is invalid."
        )

    return days


def _period_days(period: PayrollPeriod) -> int:
    days = (period.end_date - period.start_date).days + 1

    if days <= 0:
        raise PayrollCalculationDerivationError(
            "Payroll period date range is invalid."
        )

    return days


def _derive_monthly_salary_amount(
    *,
    period: PayrollPeriod,
    payroll_input: PayrollInput,
    salary_slice: PayrollInputSalarySlice,
    allow_extra_time: bool = False,
) -> Decimal:
    del period

    if payroll_input.monthly_norm_minutes is None or payroll_input.monthly_norm_minutes <= 0:
        raise PayrollCalculationDerivationError(
            "Monthly payroll requires a verified full-month norm snapshot."
        )

    if salary_slice.scheduled_minutes < 0 or salary_slice.worked_minutes < 0:
        raise PayrollCalculationDerivationError(
            "Salary slice time cannot be negative."
        )
    if salary_slice.worked_minutes > salary_slice.scheduled_minutes and not allow_extra_time:
        raise PayrollCalculationDerivationError(
            "Monthly overtime requires a separate earning; review attendance."
        )

    amount = (
        Decimal(salary_slice.salary_rate_amount)
        * Decimal(salary_slice.worked_minutes)
        / Decimal(payroll_input.monthly_norm_minutes)
    )

    return _money(amount)


def _derive_hourly_salary_amount(
    *,
    period: PayrollPeriod,
    payroll_input: PayrollInput,
    salary_slice: PayrollInputSalarySlice,
) -> Decimal:
    del period
    del payroll_input

    if salary_slice.worked_minutes < 0:
        raise PayrollCalculationDerivationError(
            "Salary slice worked minutes cannot be negative."
        )

    hours = (
        Decimal(salary_slice.worked_minutes)
        / Decimal(60)
    )

    return _money(
        Decimal(salary_slice.salary_rate_amount)
        * hours
    )


def derive_gross_calculation(
    *,
    period: PayrollPeriod,
    payroll_input: PayrollInput,
    salary_slices: list[PayrollInputSalarySlice],
    allowed_extra_slice_ids: set[int] | None = None,
) -> tuple[str, Decimal, list[dict[str, object]]]:
    if not salary_slices:
        raise PayrollCalculationDerivationError(
            "Payroll calculation requires salary slices."
        )

    currencies = {
        salary_slice.currency_code
        for salary_slice in salary_slices
    }

    if len(currencies) != 1:
        raise PayrollCalculationDerivationError(
            "Payroll calculation requires one currency."
        )

    currency_code = next(iter(currencies))

    lines: list[dict[str, object]] = []
    gross = Decimal("0.00")
    line_no = 1

    for salary_slice in salary_slices:
        rate_type = _enum_value(
            salary_slice.salary_rate_type
        )

        if rate_type == "monthly":
            amount = _derive_monthly_salary_amount(
                period=period,
                payroll_input=payroll_input,
                salary_slice=salary_slice,
                allow_extra_time=(allowed_extra_slice_ids is not None and salary_slice.id in allowed_extra_slice_ids),
            )
            quantity = _quantity(
                Decimal(_slice_days(salary_slice))
            )

        elif rate_type == "hourly":
            amount = _derive_hourly_salary_amount(
                period=period,
                payroll_input=payroll_input,
                salary_slice=salary_slice,
            )

            quantity = _quantity(
                Decimal(salary_slice.worked_minutes)
                / Decimal(60)
            )

        else:
            raise PayrollCalculationDerivationError(
                f"Unsupported salary rate type: {rate_type}"
            )

        lines.append(
            {
                "line_no": line_no,
                "line_type": "salary",
                "description": (
                    f"{rate_type} salary "
                    f"{salary_slice.effective_from.isoformat()}"
                    f".."
                    f"{salary_slice.effective_to.isoformat()}"
                ),
                "quantity": quantity,
                "rate": _quantity(
                    salary_slice.salary_rate_amount
                ),
                "amount": amount,
                "currency_code": currency_code,
                "salary_rate_type": rate_type,
                "source_salary_rate_id":
                    salary_slice.salary_rate_id,
                "source_effective_from":
                    salary_slice.effective_from,
                "source_effective_to":
                    salary_slice.effective_to,
            }
        )

        gross += amount
        line_no += 1

    adjustment = _money(
        payroll_input.manual_adjustment_amount
    )

    if adjustment != Decimal("0.00"):
        reason = (
            payroll_input.manual_adjustment_reason or ""
        ).strip()

        if not reason:
            raise PayrollCalculationDerivationError(
                "Non-zero manual adjustment requires a reason."
            )

        lines.append(
            {
                "line_no": line_no,
                "line_type": "manual_adjustment",
                "description": reason,
                "quantity": _quantity("1"),
                "rate": _quantity(adjustment),
                "amount": adjustment,
                "currency_code": currency_code,
                "salary_rate_type": None,
                "source_salary_rate_id": None,
                "source_effective_from": None,
                "source_effective_to": None,
            }
        )

        gross += adjustment

    gross = _money(gross)

    if gross < Decimal("0.00"):
        raise PayrollCalculationDerivationError(
            "Gross payroll amount cannot be negative."
        )

    line_total = _money(
        sum(
            (
                Decimal(line["amount"])
                for line in lines
            ),
            Decimal("0.00"),
        )
    )

    if line_total != gross:
        raise PayrollCalculationDerivationError(
            "Payroll calculation line total does not equal gross amount."
        )

    return currency_code, gross, lines


@serialized_payroll_mutation(PayrollCalculationLifecycleError)
async def calculate_payroll_input(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_input_id: int,
    calculated_by: int,
) -> PayrollCalculation:
    period, payroll_input, salary_slices = (
        await validate_calculation_source(
            db,
            company_id=company_id,
            payroll_input_id=payroll_input_id,
        )
    )

    existing = await get_payroll_calculation_for_input(
        db,
        company_id=company_id,
        payroll_input_id=payroll_input_id,
    )

    if existing is not None:
        return existing

    from app.services.payroll_supplement_service import derive_supplement_lines, PayrollSupplementError
    from app.services.payroll_vacation_service import derive_vacation_pay_lines, PayrollVacationError
    try:
        supplement_lines,allowed_extra=await derive_supplement_lines(db,company_id=company_id,
            payroll_input=payroll_input,period=period,salary_slices=salary_slices)
    except PayrollSupplementError as exc:
        raise PayrollCalculationDerivationError(str(exc)) from exc

    try:
        vacation_lines = await derive_vacation_pay_lines(
            db,
            company_id=company_id,
            payroll_input=payroll_input,
            period=period,
        )
    except PayrollVacationError as exc:
        raise PayrollCalculationDerivationError(str(exc)) from exc

    currency_code, gross_amount, derived_lines = (
        derive_gross_calculation(
            period=period,
            payroll_input=payroll_input,
            salary_slices=salary_slices,
            allowed_extra_slice_ids=allowed_extra,
        )
    )
    for line in supplement_lines:
        line['line_no']=len(derived_lines)+1
        derived_lines.append(line)
        gross_amount+=line['amount']

    for line in vacation_lines:
        line['line_no']=len(derived_lines)+1
        derived_lines.append(line)
        gross_amount+=line['amount']

    gross_amount=_money(gross_amount)

    calculation = PayrollCalculation(
        company_id=company_id,
        payroll_period_id=period.id,
        payroll_input_id=payroll_input.id,
        employment_contract_id=(
            payroll_input.employment_contract_id
        ),
        currency_code=currency_code,
        gross_amount=gross_amount,
        calculated_by=calculated_by,
    )

    db.add(calculation)
    await db.flush()

    for data in derived_lines:
        db.add(
            PayrollCalculationLine(
                company_id=company_id,
                payroll_calculation_id=calculation.id,
                **data,
            )
        )

    await db.flush()

    return calculation
