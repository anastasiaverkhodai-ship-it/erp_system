from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.employee import Employee, EmployeeStatus
from app.models.user import User
from app.models.user_company import user_companies


class EmployeeError(Exception):
    pass


class EmployeeCompanyNotFoundError(EmployeeError):
    pass


class EmployeeNotFoundError(EmployeeError):
    pass


class EmployeeDuplicateError(EmployeeError):
    pass


class EmployeeUserLinkError(EmployeeError):
    pass


class EmployeeLifecycleError(EmployeeError):
    pass


def normalize_employee_number(
    value: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise EmployeeError(
            "Employee number cannot be blank"
        )

    return normalized


def normalize_required_name(
    value: str,
    *,
    field_name: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise EmployeeError(
            f"{field_name} cannot be blank"
        )

    return normalized


def normalize_optional_text(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    normalized = value.strip()

    return normalized or None


async def _require_active_company(
    db: AsyncSession,
    *,
    company_id: int,
) -> Company:
    company = (
        await db.execute(
            select(Company).where(
                Company.id == company_id,
                Company.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if company is None:
        raise EmployeeCompanyNotFoundError(
            "Active company not found"
        )

    return company


async def _require_valid_user_link(
    db: AsyncSession,
    *,
    company_id: int,
    user_id: int,
) -> User:
    user = (
        await db.execute(
            select(User)
            .join(
                user_companies,
                User.id == user_companies.c.user_id,
            )
            .where(
                User.id == user_id,
                User.is_active.is_(True),
                user_companies.c.company_id == company_id,
            )
        )
    ).scalar_one_or_none()

    if user is None:
        raise EmployeeUserLinkError(
            "Linked user must be an active user "
            "assigned to this company"
        )

    return user


async def _assert_employee_number_available(
    db: AsyncSession,
    *,
    company_id: int,
    employee_number: str,
    exclude_employee_id: int | None = None,
) -> None:
    query = select(Employee.id).where(
        Employee.company_id == company_id,
        Employee.employee_number == employee_number,
    )

    if exclude_employee_id is not None:
        query = query.where(
            Employee.id != exclude_employee_id
        )

    existing = (
        await db.execute(query)
    ).scalar_one_or_none()

    if existing is not None:
        raise EmployeeDuplicateError(
            "Employee number already exists "
            "in this company"
        )


def _validate_lifecycle(
    *,
    hire_date: date,
    termination_date: date | None,
    status: EmployeeStatus,
) -> None:
    if (
        termination_date is not None
        and termination_date < hire_date
    ):
        raise EmployeeLifecycleError(
            "termination_date cannot precede hire_date"
        )

    if (
        status == EmployeeStatus.TERMINATED
        and termination_date is None
    ):
        raise EmployeeLifecycleError(
            "terminated status requires termination_date"
        )

    if (
        status != EmployeeStatus.TERMINATED
        and termination_date is not None
    ):
        raise EmployeeLifecycleError(
            "termination_date requires terminated status"
        )


async def list_employees(
    db: AsyncSession,
    *,
    company_id: int,
) -> list[Employee]:
    return list(
        (
            await db.execute(
                select(Employee)
                .where(
                    Employee.company_id == company_id
                )
                .order_by(
                    Employee.last_name.asc(),
                    Employee.first_name.asc(),
                    Employee.id.asc(),
                )
            )
        ).scalars().all()
    )


async def get_employee(
    db: AsyncSession,
    *,
    company_id: int,
    employee_id: int,
    lock_row: bool = False,
) -> Employee:
    query = select(Employee).where(
        Employee.id == employee_id,
        Employee.company_id == company_id,
    )

    if lock_row:
        query = query.with_for_update()

    employee = (
        await db.execute(query)
    ).scalar_one_or_none()

    if employee is None:
        raise EmployeeNotFoundError(
            "Employee not found"
        )

    return employee


async def create_employee(
    db: AsyncSession,
    *,
    company_id: int,
    employee_number: str,
    first_name: str,
    last_name: str,
    middle_name: str | None,
    tax_number: str | None,
    birth_date: date | None,
    hire_date: date,
    termination_date: date | None,
    status: EmployeeStatus,
    user_id: int | None,
    created_by: int,
) -> Employee:
    await _require_active_company(
        db,
        company_id=company_id,
    )

    normalized_number = normalize_employee_number(
        employee_number
    )

    await _assert_employee_number_available(
        db,
        company_id=company_id,
        employee_number=normalized_number,
    )

    if user_id is not None:
        await _require_valid_user_link(
            db,
            company_id=company_id,
            user_id=user_id,
        )

    _validate_lifecycle(
        hire_date=hire_date,
        termination_date=termination_date,
        status=status,
    )

    employee = Employee(
        company_id=company_id,
        employee_number=normalized_number,
        first_name=normalize_required_name(
            first_name,
            field_name="first_name",
        ),
        last_name=normalize_required_name(
            last_name,
            field_name="last_name",
        ),
        middle_name=normalize_optional_text(
            middle_name
        ),
        tax_number=normalize_optional_text(
            tax_number
        ),
        birth_date=birth_date,
        hire_date=hire_date,
        termination_date=termination_date,
        status=status,
        user_id=user_id,
        created_by=created_by,
    )

    db.add(employee)
    await db.flush()

    return employee


async def update_employee(
    db: AsyncSession,
    *,
    company_id: int,
    employee_id: int,
    employee_number: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    middle_name: str | None = None,
    tax_number: str | None = None,
    birth_date: date | None = None,
    hire_date: date | None = None,
    termination_date: date | None = None,
    status: EmployeeStatus | None = None,
    user_id: int | None = None,
    fields_set: set[str] | None = None,
) -> Employee:
    employee = await get_employee(
        db,
        company_id=company_id,
        employee_id=employee_id,
        lock_row=True,
    )

    supplied = fields_set or set()

    if "employee_number" in supplied:
        if employee_number is None:
            raise EmployeeError(
                "employee_number cannot be null"
            )

        normalized_number = normalize_employee_number(
            employee_number
        )

        await _assert_employee_number_available(
            db,
            company_id=company_id,
            employee_number=normalized_number,
            exclude_employee_id=employee.id,
        )

        employee.employee_number = normalized_number

    if "first_name" in supplied:
        if first_name is None:
            raise EmployeeError(
                "first_name cannot be null"
            )

        employee.first_name = normalize_required_name(
            first_name,
            field_name="first_name",
        )

    if "last_name" in supplied:
        if last_name is None:
            raise EmployeeError(
                "last_name cannot be null"
            )

        employee.last_name = normalize_required_name(
            last_name,
            field_name="last_name",
        )

    if "middle_name" in supplied:
        employee.middle_name = normalize_optional_text(
            middle_name
        )

    if "tax_number" in supplied:
        employee.tax_number = normalize_optional_text(
            tax_number
        )

    if "birth_date" in supplied:
        employee.birth_date = birth_date

    if "user_id" in supplied:
        if user_id is not None:
            await _require_valid_user_link(
                db,
                company_id=company_id,
                user_id=user_id,
            )

        employee.user_id = user_id

    next_hire_date = (
        hire_date
        if "hire_date" in supplied
        else employee.hire_date
    )

    if next_hire_date is None:
        raise EmployeeLifecycleError(
            "hire_date cannot be null"
        )

    next_termination_date = (
        termination_date
        if "termination_date" in supplied
        else employee.termination_date
    )

    next_status = (
        status
        if "status" in supplied
        else employee.status
    )

    if next_status is None:
        raise EmployeeLifecycleError(
            "status cannot be null"
        )

    _validate_lifecycle(
        hire_date=next_hire_date,
        termination_date=next_termination_date,
        status=next_status,
    )

    employee.hire_date = next_hire_date
    employee.termination_date = next_termination_date
    employee.status = next_status

    await db.flush()

    return employee
