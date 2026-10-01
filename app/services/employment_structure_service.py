from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.department import Department
from app.models.employee import Employee, EmployeeStatus
from app.services.hr_change_service import snapshot, record_change
from app.models.employment_contract import (
    EmploymentContract,
    EmploymentContractStatus,
    WorkArrangement,
)
from app.models.position import Position


class EmploymentStructureError(Exception):
    pass


class EmploymentStructureNotFoundError(
    EmploymentStructureError
):
    pass


class EmploymentStructureDuplicateError(
    EmploymentStructureError
):
    pass


class EmploymentStructureLifecycleError(
    EmploymentStructureError
):
    pass


def _required_text(
    value: str,
    *,
    field_name: str,
) -> str:
    normalized = value.strip()
    if not normalized:
        raise EmploymentStructureError(
            f"{field_name} cannot be blank"
        )
    return normalized


async def _require_active_company(
    db: AsyncSession,
    *,
    company_id: int,
) -> None:
    company_id_found = (
        await db.execute(
            select(Company.id).where(
                Company.id == company_id,
                Company.is_active.is_(True),
            ).with_for_update()
        )
    ).scalar_one_or_none()

    if company_id_found is None:
        raise EmploymentStructureNotFoundError(
            "Active company not found"
        )


async def _require_employee(
    db: AsyncSession,
    *,
    company_id: int,
    employee_id: int,
) -> Employee:
    employee = (
        await db.execute(
            select(Employee).where(
                Employee.company_id == company_id,
                Employee.id == employee_id,
            ).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()

    if employee is None:
        raise EmploymentStructureNotFoundError(
            "Employee not found"
        )

    return employee


async def _require_department(
    db: AsyncSession,
    *,
    company_id: int,
    department_id: int,
    active_only: bool = False,
) -> Department:
    query = select(Department).where(
        Department.company_id == company_id,
        Department.id == department_id,
    )

    if active_only:
        query = query.where(
            Department.is_active.is_(True)
        )

    department = (
        await db.execute(query.execution_options(populate_existing=True))
    ).scalar_one_or_none()

    if department is None:
        raise EmploymentStructureNotFoundError(
            "Department not found"
        )

    return department


async def _require_position(
    db: AsyncSession,
    *,
    company_id: int,
    position_id: int,
    active_only: bool = False,
) -> Position:
    query = select(Position).where(
        Position.company_id == company_id,
        Position.id == position_id,
    )

    if active_only:
        query = query.where(
            Position.is_active.is_(True)
        )

    position = (
        await db.execute(query.execution_options(populate_existing=True))
    ).scalar_one_or_none()

    if position is None:
        raise EmploymentStructureNotFoundError(
            "Position not found"
        )

    return position


async def _assert_code_available(
    db: AsyncSession,
    *,
    model,
    company_id: int,
    code: str,
    exclude_id: int | None = None,
) -> None:
    query = select(model.id).where(
        model.company_id == company_id,
        model.code == code,
    )

    if exclude_id is not None:
        query = query.where(model.id != exclude_id)

    existing = (
        await db.execute(query)
    ).scalar_one_or_none()

    if existing is not None:
        raise EmploymentStructureDuplicateError(
            "Code already exists in this company"
        )


async def _assert_contract_number_available(
    db: AsyncSession,
    *,
    company_id: int,
    contract_number: str,
    exclude_id: int | None = None,
) -> None:
    query = select(EmploymentContract.id).where(
        EmploymentContract.company_id == company_id,
        EmploymentContract.contract_number
        == contract_number,
    )

    if exclude_id is not None:
        query = query.where(
            EmploymentContract.id != exclude_id
        )

    existing = (
        await db.execute(query)
    ).scalar_one_or_none()

    if existing is not None:
        raise EmploymentStructureDuplicateError(
            "Employment contract number already exists "
            "in this company"
        )


def _validate_contract_lifecycle(
    *,
    start_date: date,
    end_date: date | None,
    status: EmploymentContractStatus,
) -> None:
    if end_date is not None and end_date < start_date:
        raise EmploymentStructureLifecycleError(
            "end_date cannot precede start_date"
        )

    if (
        status == EmploymentContractStatus.ACTIVE
        and end_date is not None
    ):
        raise EmploymentStructureLifecycleError(
            "active status requires end_date to be null"
        )

    if (
        status == EmploymentContractStatus.ENDED
        and end_date is None
    ):
        raise EmploymentStructureLifecycleError(
            "ended status requires end_date"
        )


async def list_departments(
    db: AsyncSession,
    *,
    company_id: int,
) -> list[Department]:
    return list(
        (
            await db.execute(
                select(Department)
                .where(Department.company_id == company_id)
                .order_by(
                    Department.code.asc(),
                    Department.id.asc(),
                )
            )
        ).scalars().all()
    )


async def get_department(
    db: AsyncSession,
    *,
    company_id: int,
    department_id: int,
    lock_row: bool = False,
) -> Department:
    query = select(Department).where(
        Department.company_id == company_id,
        Department.id == department_id,
    )

    if lock_row:
        query = query.with_for_update().execution_options(populate_existing=True)

    department = (
        await db.execute(query.execution_options(populate_existing=True))
    ).scalar_one_or_none()

    if department is None:
        raise EmploymentStructureNotFoundError(
            "Department not found"
        )

    return department


async def create_department(
    db: AsyncSession,
    *,
    company_id: int,
    code: str,
    name: str,
    parent_id: int | None,
    created_by: int,
) -> Department:
    await _require_active_company(
        db,
        company_id=company_id,
    )

    normalized_code = _required_text(
        code,
        field_name="code",
    )
    normalized_name = _required_text(
        name,
        field_name="name",
    )

    await _assert_code_available(
        db,
        model=Department,
        company_id=company_id,
        code=normalized_code,
    )

    if parent_id is not None:
        await _require_department(
            db,
            company_id=company_id,
            department_id=parent_id,
            active_only=True,
        )

    department = Department(
        company_id=company_id,
        code=normalized_code,
        name=normalized_name,
        parent_id=parent_id,
        created_by=created_by,
    )

    db.add(department)
    await db.flush()
    await record_change(db, department, "department", None, created_by)
    return department


async def update_department(
    db: AsyncSession,
    *,
    company_id: int,
    department_id: int,
    code: str | None = None,
    name: str | None = None,
    parent_id: int | None = None,
    is_active: bool | None = None,
    fields_set: set[str] | None = None,
    changed_by: int | None = None,
) -> Department:
    await _require_active_company(db, company_id=company_id)
    department = await get_department(
        db,
        company_id=company_id,
        department_id=department_id,
        lock_row=True,
    )

    before = snapshot(department)
    supplied = fields_set or set()

    if "code" in supplied:
        if code is None:
            raise EmploymentStructureError(
                "code cannot be null"
            )

        normalized_code = _required_text(
            code,
            field_name="code",
        )

        await _assert_code_available(
            db,
            model=Department,
            company_id=company_id,
            code=normalized_code,
            exclude_id=department.id,
        )
        department.code = normalized_code

    if "name" in supplied:
        if name is None:
            raise EmploymentStructureError(
                "name cannot be null"
            )
        department.name = _required_text(
            name,
            field_name="name",
        )

    if "parent_id" in supplied:
        if parent_id == department.id:
            raise EmploymentStructureLifecycleError(
                "Department cannot be its own parent"
            )

        if parent_id is not None:
            await _require_department(
                db,
                company_id=company_id,
                department_id=parent_id,
                active_only=True,
            )

        cursor = parent_id
        visited = {department.id}
        while cursor is not None:
            if cursor in visited:
                raise EmploymentStructureLifecycleError("Department hierarchy would contain a cycle")
            visited.add(cursor)
            ancestor = await _require_department(db, company_id=company_id, department_id=cursor)
            cursor = ancestor.parent_id
        department.parent_id = parent_id

    if "is_active" in supplied:
        if is_active is None:
            raise EmploymentStructureError(
                "is_active cannot be null"
            )
        if is_active and department.parent_id is not None:
            await _require_department(db, company_id=company_id, department_id=department.parent_id, active_only=True)
        if not is_active:
            child = await db.scalar(select(Department.id).where(Department.company_id == company_id,
                Department.parent_id == department.id, Department.is_active.is_(True)).limit(1))
            used = await db.scalar(select(EmploymentContract.id).where(EmploymentContract.company_id == company_id,
                EmploymentContract.department_id == department.id, EmploymentContract.status == EmploymentContractStatus.ACTIVE).limit(1))
            if child or used:
                raise EmploymentStructureLifecycleError("Department has active children or contracts")
        department.is_active = is_active

    await db.flush()
    await record_change(db, department, "department", before, changed_by)
    return department


async def list_positions(
    db: AsyncSession,
    *,
    company_id: int,
) -> list[Position]:
    return list(
        (
            await db.execute(
                select(Position)
                .where(Position.company_id == company_id)
                .order_by(
                    Position.code.asc(),
                    Position.id.asc(),
                )
            )
        ).scalars().all()
    )


async def get_position(
    db: AsyncSession,
    *,
    company_id: int,
    position_id: int,
    lock_row: bool = False,
) -> Position:
    query = select(Position).where(
        Position.company_id == company_id,
        Position.id == position_id,
    )

    if lock_row:
        query = query.with_for_update().execution_options(populate_existing=True)

    position = (
        await db.execute(query.execution_options(populate_existing=True))
    ).scalar_one_or_none()

    if position is None:
        raise EmploymentStructureNotFoundError(
            "Position not found"
        )

    return position


async def create_position(
    db: AsyncSession,
    *,
    company_id: int,
    code: str,
    name: str,
    created_by: int,
) -> Position:
    await _require_active_company(
        db,
        company_id=company_id,
    )

    normalized_code = _required_text(
        code,
        field_name="code",
    )
    normalized_name = _required_text(
        name,
        field_name="name",
    )

    await _assert_code_available(
        db,
        model=Position,
        company_id=company_id,
        code=normalized_code,
    )

    position = Position(
        company_id=company_id,
        code=normalized_code,
        name=normalized_name,
        created_by=created_by,
    )

    db.add(position)
    await db.flush()
    await record_change(db, position, "position", None, created_by)
    return position


async def update_position(
    db: AsyncSession,
    *,
    company_id: int,
    position_id: int,
    code: str | None = None,
    name: str | None = None,
    is_active: bool | None = None,
    fields_set: set[str] | None = None,
    changed_by: int | None = None,
) -> Position:
    await _require_active_company(db, company_id=company_id)
    position = await get_position(
        db,
        company_id=company_id,
        position_id=position_id,
        lock_row=True,
    )

    before = snapshot(position)
    supplied = fields_set or set()

    if "code" in supplied:
        if code is None:
            raise EmploymentStructureError(
                "code cannot be null"
            )

        normalized_code = _required_text(
            code,
            field_name="code",
        )

        await _assert_code_available(
            db,
            model=Position,
            company_id=company_id,
            code=normalized_code,
            exclude_id=position.id,
        )
        position.code = normalized_code

    if "name" in supplied:
        if name is None:
            raise EmploymentStructureError(
                "name cannot be null"
            )
        position.name = _required_text(
            name,
            field_name="name",
        )

    if "is_active" in supplied:
        if is_active is None:
            raise EmploymentStructureError(
                "is_active cannot be null"
            )
        if not is_active and await db.scalar(select(EmploymentContract.id).where(
            EmploymentContract.company_id == company_id, EmploymentContract.position_id == position.id,
            EmploymentContract.status == EmploymentContractStatus.ACTIVE).limit(1)):
            raise EmploymentStructureLifecycleError("Position has active contracts")
        position.is_active = is_active

    await db.flush()
    await record_change(db, position, "position", before, changed_by)
    return position


async def list_employment_contracts(
    db: AsyncSession,
    *,
    company_id: int,
    employee_id: int | None = None,
) -> list[EmploymentContract]:
    query = select(EmploymentContract).where(
        EmploymentContract.company_id == company_id
    )

    if employee_id is not None:
        query = query.where(
            EmploymentContract.employee_id == employee_id
        )

    query = query.order_by(
        EmploymentContract.start_date.desc(),
        EmploymentContract.id.desc(),
    )

    return list(
        (await db.execute(query)).scalars().all()
    )


async def get_employment_contract(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int,
    lock_row: bool = False,
) -> EmploymentContract:
    query = select(EmploymentContract).where(
        EmploymentContract.company_id == company_id,
        EmploymentContract.id == contract_id,
    )

    if lock_row:
        query = query.with_for_update().execution_options(populate_existing=True)

    contract = (
        await db.execute(query)
    ).scalar_one_or_none()

    if contract is None:
        raise EmploymentStructureNotFoundError(
            "Employment contract not found"
        )

    return contract


async def _assert_no_contract_overlap(
    db: AsyncSession,
    *,
    company_id: int,
    employee_id: int,
    start_date: date,
    end_date: date | None,
    status: EmploymentContractStatus,
    exclude_id: int | None = None,
) -> None:
    if status == EmploymentContractStatus.CANCELLED:
        return

    query = select(EmploymentContract.id).where(
        EmploymentContract.company_id == company_id,
        EmploymentContract.employee_id == employee_id,
        EmploymentContract.status
        != EmploymentContractStatus.CANCELLED,
    )

    if end_date is not None:
        query = query.where(
            EmploymentContract.start_date <= end_date
        )

    query = query.where(
        (
            EmploymentContract.end_date.is_(None)
        )
        | (
            EmploymentContract.end_date >= start_date
        )
    )

    if exclude_id is not None:
        query = query.where(
            EmploymentContract.id != exclude_id
        )

    existing = (
        await db.execute(query.limit(1))
    ).scalar_one_or_none()

    if existing is not None:
        raise EmploymentStructureDuplicateError(
            "Employment contract overlaps another "
            "non-cancelled contract for this employee"
        )


async def create_employment_contract(
    db: AsyncSession,
    *,
    company_id: int,
    employee_id: int,
    contract_number: str,
    contract_type: str,
    department_id: int | None,
    position_id: int | None,
    work_arrangement: WorkArrangement,
    start_date: date,
    end_date: date | None,
    status: EmploymentContractStatus,
    created_by: int,
) -> EmploymentContract:
    await _require_active_company(
        db,
        company_id=company_id,
    )

    await _require_employee(
        db,
        company_id=company_id,
        employee_id=employee_id,
    )

    if department_id is not None:
        await _require_department(
            db,
            company_id=company_id,
            department_id=department_id,
            active_only=True,
        )

    if position_id is not None:
        await _require_position(
            db,
            company_id=company_id,
            position_id=position_id,
            active_only=True,
        )

    normalized_number = _required_text(
        contract_number,
        field_name="contract_number",
    )
    normalized_type = _required_text(
        contract_type,
        field_name="contract_type",
    )

    await _assert_contract_number_available(
        db,
        company_id=company_id,
        contract_number=normalized_number,
    )

    _validate_contract_lifecycle(
        start_date=start_date,
        end_date=end_date,
        status=status,
    )

    await _validate_employee_dates(db, company_id, employee_id, start_date, end_date, status)
    await _assert_no_contract_overlap(
        db,
        company_id=company_id,
        employee_id=employee_id,
        start_date=start_date,
        end_date=end_date,
        status=status,
    )

    contract = EmploymentContract(
        company_id=company_id,
        employee_id=employee_id,
        contract_number=normalized_number,
        contract_type=normalized_type,
        department_id=department_id,
        position_id=position_id,
        work_arrangement=work_arrangement,
        start_date=start_date,
        end_date=end_date,
        status=status,
        created_by=created_by,
    )

    db.add(contract)
    await db.flush()
    await record_change(db, contract, "employment_contract", None, created_by)
    return contract


async def update_employment_contract(
    db: AsyncSession,
    *,
    company_id: int,
    contract_id: int,
    contract_number: str | None = None,
    contract_type: str | None = None,
    department_id: int | None = None,
    position_id: int | None = None,
    work_arrangement: WorkArrangement | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    status: EmploymentContractStatus | None = None,
    fields_set: set[str] | None = None,
    changed_by: int | None = None,
) -> EmploymentContract:
    await _require_active_company(db, company_id=company_id)
    contract = await get_employment_contract(
        db,
        company_id=company_id,
        contract_id=contract_id,
        lock_row=True,
    )

    before = snapshot(contract)
    supplied = fields_set or set()

    if "contract_number" in supplied:
        if contract_number is None:
            raise EmploymentStructureError(
                "contract_number cannot be null"
            )

        normalized_number = _required_text(
            contract_number,
            field_name="contract_number",
        )

        await _assert_contract_number_available(
            db,
            company_id=company_id,
            contract_number=normalized_number,
            exclude_id=contract.id,
        )

        contract.contract_number = normalized_number

    if "contract_type" in supplied:
        if contract_type is None:
            raise EmploymentStructureError(
                "contract_type cannot be null"
            )

        contract.contract_type = _required_text(
            contract_type,
            field_name="contract_type",
        )

    if "department_id" in supplied:
        if department_id is not None:
            await _require_department(
                db,
                company_id=company_id,
                department_id=department_id,
                active_only=True,
            )
        contract.department_id = department_id

    if "position_id" in supplied:
        if position_id is not None:
            await _require_position(
                db,
                company_id=company_id,
                position_id=position_id,
                active_only=True,
            )
        contract.position_id = position_id

    next_start = (
        start_date
        if "start_date" in supplied
        else contract.start_date
    )
    next_end = (
        end_date
        if "end_date" in supplied
        else contract.end_date
    )
    next_status = (
        status
        if "status" in supplied
        else contract.status
    )

    if next_start is None:
        raise EmploymentStructureLifecycleError(
            "start_date cannot be null"
        )

    if next_status is None:
        raise EmploymentStructureLifecycleError(
            "status cannot be null"
        )

    _validate_contract_lifecycle(
        start_date=next_start,
        end_date=next_end,
        status=next_status,
    )

    if next_status == EmploymentContractStatus.ACTIVE:
        if contract.department_id is not None:
            await _require_department(db, company_id=company_id, department_id=contract.department_id, active_only=True)
        if contract.position_id is not None:
            await _require_position(db, company_id=company_id, position_id=contract.position_id, active_only=True)
    await _validate_employee_dates(db, company_id, contract.employee_id, next_start, next_end, next_status)
    await _assert_no_contract_overlap(
        db,
        company_id=company_id,
        employee_id=contract.employee_id,
        start_date=next_start,
        end_date=next_end,
        status=next_status,
        exclude_id=contract.id,
    )

    contract.start_date = next_start
    contract.end_date = next_end
    contract.status = next_status

    if "work_arrangement" in supplied:
        if work_arrangement is None:
            raise EmploymentStructureError(
                "work_arrangement cannot be null"
            )
        contract.work_arrangement = work_arrangement

    await db.flush()
    await record_change(db, contract, "employment_contract", before, changed_by)
    return contract


async def _validate_employee_dates(db, company_id, employee_id, start, end, status):
    employee = await _require_employee(db, company_id=company_id, employee_id=employee_id)
    if status == EmploymentContractStatus.CANCELLED:
        return
    if start < employee.hire_date:
        raise EmploymentStructureLifecycleError("Contract starts before employee hire date")
    if employee.termination_date is not None and (end is None or end > employee.termination_date):
        raise EmploymentStructureLifecycleError("Contract extends beyond employee termination date")
    if status == EmploymentContractStatus.ACTIVE and employee.status != EmployeeStatus.ACTIVE:
        raise EmploymentStructureLifecycleError("Active contract requires an active employee")
