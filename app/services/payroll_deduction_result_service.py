from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import PayrollCalculation, PayrollPeriod
from app.models.payroll_deduction import PayrollDeductionInstruction
from app.models.payroll_deduction_result import (
    PayrollDeductionResult,
    PayrollDeductionResultLine,
)
from app.models.payroll_statutory import PayrollStatutoryResult


class PayrollDeductionResultError(ValueError):
    pass


class PayrollDeductionResultNotFoundError(
    PayrollDeductionResultError
):
    pass


class PayrollDeductionResultConflictError(
    PayrollDeductionResultError
):
    pass


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


async def get_payroll_deduction_result(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> PayrollDeductionResult | None:
    return await db.scalar(
        select(PayrollDeductionResult).where(
            PayrollDeductionResult.company_id == company_id,
            PayrollDeductionResult.payroll_calculation_id
            == payroll_calculation_id,
        )
    )


async def list_payroll_deduction_result_lines(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_deduction_result_id: int,
) -> list[PayrollDeductionResultLine]:
    return list(
        (
            await db.scalars(
                select(PayrollDeductionResultLine)
                .where(
                    PayrollDeductionResultLine.company_id
                    == company_id,
                    PayrollDeductionResultLine
                    .payroll_deduction_result_id
                    == payroll_deduction_result_id,
                )
                .order_by(
                    PayrollDeductionResultLine.line_no,
                    PayrollDeductionResultLine.id,
                )
            )
        ).all()
    )


async def calculate_payroll_deduction_result(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
    calculated_by: int,
) -> PayrollDeductionResult:
    existing = await get_payroll_deduction_result(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )
    if existing is not None:
        return existing

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
        )
    )
    if calculation is None:
        raise PayrollDeductionResultNotFoundError(
            "Payroll calculation not found"
        )

    statutory = await db.scalar(
        select(PayrollStatutoryResult).where(
            PayrollStatutoryResult.company_id == company_id,
            PayrollStatutoryResult.payroll_calculation_id
            == calculation.id,
        )
    )
    if statutory is None:
        raise PayrollDeductionResultNotFoundError(
            "Payroll statutory result is required before deductions"
        )

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == calculation.payroll_period_id,
        )
    )
    if period is None:
        raise PayrollDeductionResultNotFoundError(
            "Payroll period not found"
        )

    effective_date = period.end_date

    statutory_net = _money(
        Decimal(statutory.net_amount)
    )

    if statutory_net < Decimal("0.00"):
        raise PayrollDeductionResultError(
            "Statutory net amount cannot be negative"
        )

    currency = str(statutory.currency_code).upper()

    instructions = list(
        (
            await db.scalars(
                select(PayrollDeductionInstruction)
                .where(
                    PayrollDeductionInstruction.company_id
                    == company_id,
                    PayrollDeductionInstruction
                    .employment_contract_id
                    == calculation.employment_contract_id,
                    PayrollDeductionInstruction.effective_from
                    <= effective_date,
                    (
                        PayrollDeductionInstruction.effective_to
                        .is_(None)
                        | (
                            PayrollDeductionInstruction.effective_to
                            >= effective_date
                        )
                    ),
                )
                .order_by(
                    PayrollDeductionInstruction.priority,
                    PayrollDeductionInstruction.id,
                )
            )
        ).all()
    )

    result = PayrollDeductionResult(
        company_id=company_id,
        payroll_calculation_id=calculation.id,
        payroll_statutory_result_id=statutory.id,
        employment_contract_id=calculation.employment_contract_id,
        statutory_net_amount=statutory_net,
        deduction_amount=Decimal("0.00"),
        final_payable_amount=statutory_net,
        currency_code=currency,
        calculated_by=calculated_by,
        calculated_at=datetime.now(UTC),
    )

    db.add(result)
    await db.flush()

    remaining = statutory_net
    total = Decimal("0.00")
    line_no = 1

    for instruction in instructions:
        instruction_currency = str(
            instruction.currency_code
        ).upper()

        if instruction_currency != currency:
            raise PayrollDeductionResultError(
                "Payroll deduction instruction currency "
                "does not match statutory result currency"
            )

        if instruction.method == "fixed":
            amount = _money(
                Decimal(instruction.fixed_amount)
            )
        elif instruction.method == "percentage":
            amount = _money(
                statutory_net
                * Decimal(instruction.percentage)
                / Decimal("100")
            )
        else:
            raise PayrollDeductionResultError(
                "Unsupported payroll deduction method"
            )

        if amount <= Decimal("0.00"):
            raise PayrollDeductionResultError(
                "Payroll deduction amount must be positive"
            )

        if amount > remaining:
            raise PayrollDeductionResultError(
                "Payroll deductions exceed statutory net pay"
            )

        line = PayrollDeductionResultLine(
            company_id=company_id,
            payroll_deduction_result_id=result.id,
            payroll_deduction_instruction_id=instruction.id,
            line_no=line_no,
            deduction_type=instruction.deduction_type,
            method=instruction.method,
            calculation_base_amount=statutory_net,
            fixed_amount=instruction.fixed_amount,
            percentage=instruction.percentage,
            amount=amount,
            priority=instruction.priority,
            currency_code=currency,
            source_reference=instruction.source_reference,
            created_at=result.calculated_at,
        )

        db.add(line)

        total = _money(total + amount)
        remaining = _money(remaining - amount)
        line_no += 1

    result.deduction_amount = total
    result.final_payable_amount = remaining

    await db.flush()

    if (
        _money(
            result.statutory_net_amount
            - result.deduction_amount
        )
        != result.final_payable_amount
    ):
        raise PayrollDeductionResultError(
            "Payroll deduction result does not reconcile"
        )

    return result
