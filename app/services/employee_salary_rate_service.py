from datetime import date

from fastapi import HTTPException
from app.services.payroll_mutation_guard import serialized_payroll_mutation, ensure_payroll_source_editable


def _payroll_conflict(message):
    return HTTPException(status_code=409, detail=message)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.employment_contract import (
    EmploymentContract,
    EmploymentContractStatus,
)
from app.schemas.employee_salary_rate import (
    SalaryRateCreate,
    SalaryRateUpdate,
)
from app.services.hr_change_service import record_change, snapshot


def _ranges_overlap(
    left_from: date,
    left_to: date | None,
    right_from: date,
    right_to: date | None,
) -> bool:
    return (
        left_from <= (right_to or date.max)
        and right_from <= (left_to or date.max)
    )


async def _get_contract(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int,
    lock: bool = False,
) -> EmploymentContract:
    stmt = select(EmploymentContract).where(
        EmploymentContract.company_id == company_id,
        EmploymentContract.id == contract_id,
    )

    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)

    contract = (await db.execute(stmt)).scalar_one_or_none()

    if contract is None:
        raise HTTPException(
            status_code=404,
            detail="Employment contract not found",
        )

    return contract


def _validate_contract_window(
    contract: EmploymentContract,
    effective_from: date,
    effective_to: date | None,
) -> None:
    if contract.status == EmploymentContractStatus.CANCELLED:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cancelled employment contract cannot "
                "receive salary rates"
            ),
        )

    if effective_to is not None and effective_to < effective_from:
        raise HTTPException(
            status_code=400,
            detail="effective_to must be on or after effective_from",
        )

    if effective_from < contract.start_date:
        raise HTTPException(
            status_code=400,
            detail=(
                "Salary rate cannot start before "
                "employment contract"
            ),
        )

    if contract.end_date is not None:
        if effective_from > contract.end_date:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Salary rate cannot start after "
                    "employment contract end"
                ),
            )

        if effective_to is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Ended employment contract requires "
                    "bounded salary rate"
                ),
            )

        if effective_to > contract.end_date:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Salary rate cannot extend beyond "
                    "employment contract"
                ),
            )


async def _assert_no_overlap(
    db: AsyncSession,
    *,
    company_id: int,
    employment_contract_id: int,
    effective_from: date,
    effective_to: date | None,
    exclude_id: int | None = None,
) -> None:
    stmt = (
        select(EmployeeSalaryRate)
        .where(
            EmployeeSalaryRate.company_id == company_id,
            EmployeeSalaryRate.employment_contract_id
            == employment_contract_id,
        )
        .with_for_update()
    )

    if exclude_id is not None:
        stmt = stmt.where(
            EmployeeSalaryRate.id != exclude_id
        )

    rows = (await db.execute(stmt)).scalars().all()

    for row in rows:
        if _ranges_overlap(
            effective_from,
            effective_to,
            row.effective_from,
            row.effective_to,
        ):
            raise HTTPException(
                status_code=409,
                detail="Salary rate overlaps an existing rate",
            )


async def list_salary_rates(
    db: AsyncSession,
    *,
    company_id: int,
    employment_contract_id: int | None = None,
    employee_id: int | None = None,
) -> list[EmployeeSalaryRate]:
    stmt = select(EmployeeSalaryRate).where(
        EmployeeSalaryRate.company_id == company_id
    )

    if employment_contract_id is not None:
        stmt = stmt.where(
            EmployeeSalaryRate.employment_contract_id
            == employment_contract_id
        )

    if employee_id is not None:
        stmt = stmt.join(
            EmploymentContract,
            (
                EmploymentContract.company_id
                == EmployeeSalaryRate.company_id
            )
            & (
                EmploymentContract.id
                == EmployeeSalaryRate.employment_contract_id
            ),
        ).where(
            EmploymentContract.company_id == company_id,
            EmploymentContract.employee_id == employee_id,
        )

    stmt = stmt.order_by(
        EmployeeSalaryRate.effective_from,
        EmployeeSalaryRate.id,
    )

    return list(
        (await db.execute(stmt)).scalars().all()
    )


async def get_salary_rate(
    db: AsyncSession,
    *,
    company_id: int,
    salary_rate_id: int,
    lock: bool = False,
) -> EmployeeSalaryRate:
    stmt = select(EmployeeSalaryRate).where(
        EmployeeSalaryRate.company_id == company_id,
        EmployeeSalaryRate.id == salary_rate_id,
    )

    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)

    row = (await db.execute(stmt)).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Salary rate not found",
        )

    return row


@serialized_payroll_mutation(_payroll_conflict)
async def create_salary_rate(
    db: AsyncSession,
    *,
    company_id: int,
    created_by: int,
    data: SalaryRateCreate,
) -> EmployeeSalaryRate:
    contract = await _get_contract(
        db,
        company_id=company_id,
        contract_id=data.employment_contract_id,
        lock=True,
    )

    await ensure_payroll_source_editable(db, company_id=company_id,
        contract_id=contract.id, date_from=data.effective_from, date_to=data.effective_to,
        error_type=_payroll_conflict)

    _validate_contract_window(
        contract,
        data.effective_from,
        data.effective_to,
    )

    await _assert_no_overlap(
        db,
        company_id=company_id,
        employment_contract_id=contract.id,
        effective_from=data.effective_from,
        effective_to=data.effective_to,
    )

    row = EmployeeSalaryRate(
        company_id=company_id,
        employment_contract_id=contract.id,
        rate_type=data.rate_type,
        amount=data.amount,
        currency_code=data.currency_code,
        effective_from=data.effective_from,
        effective_to=data.effective_to,
        created_by=created_by,
    )

    db.add(row)
    await db.flush()

    await record_change(
        db,
        row,
        "salary_rate",
        None,
        created_by,
    )

    await db.flush()

    return row


@serialized_payroll_mutation(_payroll_conflict)
async def update_salary_rate(
    db: AsyncSession,
    *,
    company_id: int,
    salary_rate_id: int,
    changed_by: int,
    data: SalaryRateUpdate,
) -> EmployeeSalaryRate:
    row = await get_salary_rate(
        db,
        company_id=company_id,
        salary_rate_id=salary_rate_id,
        lock=True,
    )

    contract = await _get_contract(
        db,
        company_id=company_id,
        contract_id=row.employment_contract_id,
        lock=True,
    )

    before = snapshot(row)

    values = data.model_dump(exclude_unset=True)

    effective_from = values.get(
        "effective_from",
        row.effective_from,
    )
    effective_to = values.get(
        "effective_to",
        row.effective_to,
    )

    for start, end in [(row.effective_from, row.effective_to), (effective_from, effective_to)]:
        await ensure_payroll_source_editable(db, company_id=company_id,
            contract_id=contract.id, date_from=start, date_to=end, error_type=_payroll_conflict)

    _validate_contract_window(
        contract,
        effective_from,
        effective_to,
    )

    await _assert_no_overlap(
        db,
        company_id=company_id,
        employment_contract_id=row.employment_contract_id,
        effective_from=effective_from,
        effective_to=effective_to,
        exclude_id=row.id,
    )

    for field, value in values.items():
        setattr(row, field, value)

    await db.flush()

    await record_change(
        db,
        row,
        "salary_rate",
        before,
        changed_by,
    )

    await db.flush()

    return row
