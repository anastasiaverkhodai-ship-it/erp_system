from __future__ import annotations

from app.services.payroll_mutation_guard import serialized_payroll_mutation

from datetime import UTC, date, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employment_contract import EmploymentContract
from app.models.payroll_deduction import PayrollDeductionInstruction
from app.schemas.payroll_deduction import PayrollDeductionInstructionCreate


class PayrollDeductionError(ValueError):
    pass


class PayrollDeductionNotFoundError(PayrollDeductionError):
    pass


class PayrollDeductionConflictError(PayrollDeductionError):
    pass


async def get_payroll_deduction_instruction(
    db: AsyncSession,
    *,
    company_id: int,
    instruction_id: int,
) -> PayrollDeductionInstruction | None:
    return await db.scalar(
        select(PayrollDeductionInstruction).where(
            PayrollDeductionInstruction.company_id == company_id,
            PayrollDeductionInstruction.id == instruction_id,
        )
    )


async def list_payroll_deduction_instructions(
    db: AsyncSession,
    *,
    company_id: int,
    employment_contract_id: int,
    active_on: date | None = None,
) -> list[PayrollDeductionInstruction]:
    query = select(PayrollDeductionInstruction).where(
        PayrollDeductionInstruction.company_id == company_id,
        PayrollDeductionInstruction.employment_contract_id
        == employment_contract_id,
    )

    if active_on is not None:
        query = query.where(
            PayrollDeductionInstruction.effective_from <= active_on,
            (
                PayrollDeductionInstruction.effective_to.is_(None)
                | (
                    PayrollDeductionInstruction.effective_to
                    >= active_on
                )
            ),
        )

    query = query.order_by(
        PayrollDeductionInstruction.priority,
        PayrollDeductionInstruction.id,
    )

    return list((await db.scalars(query)).all())


@serialized_payroll_mutation(PayrollDeductionError)
async def create_payroll_deduction_instruction(
    db: AsyncSession,
    *,
    company_id: int,
    data: PayrollDeductionInstructionCreate,
    created_by: int,
) -> PayrollDeductionInstruction:
    existing = await db.scalar(
        select(PayrollDeductionInstruction).where(
            PayrollDeductionInstruction.company_id == company_id,
            PayrollDeductionInstruction.employment_contract_id
            == data.employment_contract_id,
            PayrollDeductionInstruction.request_key
            == data.request_key,
        )
    )

    if existing is not None:
        same = (
            existing.deduction_type == data.deduction_type
            and existing.method == data.method
            and existing.fixed_amount == data.fixed_amount
            and existing.percentage == data.percentage
            and existing.currency_code == data.currency_code.upper()
            and existing.priority == data.priority
            and existing.effective_from == data.effective_from
            and existing.effective_to == data.effective_to
            and existing.source_reference == data.source_reference
        )
        if not same:
            raise PayrollDeductionConflictError(
                "request_key already exists with different deduction data"
            )
        return existing

    from app.models.payroll_deduction_result import PayrollDeductionResult
    from app.models.payroll import PayrollCalculation, PayrollPeriod
    frozen = await db.scalar(select(PayrollDeductionResult.id)
        .join(PayrollCalculation, PayrollCalculation.id == PayrollDeductionResult.payroll_calculation_id)
        .join(PayrollPeriod, PayrollPeriod.id == PayrollCalculation.payroll_period_id)
        .where(PayrollDeductionResult.company_id == company_id,
            PayrollDeductionResult.employment_contract_id == data.employment_contract_id,
            PayrollPeriod.end_date >= data.effective_from,
            PayrollPeriod.end_date <= (data.effective_to or date.max)).limit(1))
    if frozen is not None:
        raise PayrollDeductionConflictError('Deduction period already calculated; use a correction')

    contract = await db.scalar(
        select(EmploymentContract).where(
            EmploymentContract.company_id == company_id,
            EmploymentContract.id == data.employment_contract_id,
        )
    )
    if contract is None:
        raise PayrollDeductionNotFoundError(
            "Employment contract not found"
        )

    if (
        data.effective_to is not None
        and data.effective_to < data.effective_from
    ):
        raise PayrollDeductionError(
            "effective_to cannot precede effective_from"
        )

    row = PayrollDeductionInstruction(
        company_id=company_id,
        employment_contract_id=data.employment_contract_id,
        deduction_type=data.deduction_type.strip(),
        method=data.method,
        fixed_amount=data.fixed_amount,
        percentage=data.percentage,
        currency_code=data.currency_code.upper(),
        priority=data.priority,
        effective_from=data.effective_from,
        effective_to=data.effective_to,
        request_key=data.request_key.strip(),
        source_reference=(
            data.source_reference.strip()
            if data.source_reference
            else None
        ),
        created_by=created_by,
        created_at=datetime.now(UTC),
    )
    db.add(row)
    await db.flush()
    return row
