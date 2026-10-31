"""Independent ORM verification of current payroll statutory sources.

The verifier reloads underlying records rather than trusting a supplied
readiness payload. It does not approve statutory mappings or generate
filing-ready forms.
"""

from decimal import Decimal, InvalidOperation

from sqlalchemy import select, and_

from app.models.payroll import PayrollCalculation
from app.models.payroll_statutory import (
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)
from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.services.payroll_revision_service import (
    current_calculation_filter,
)


class PersistedSourceIntegrityError(ValueError):
    pass


def reject(message):
    raise PersistedSourceIntegrityError(message)


def money(value):
    if value is None or isinstance(value, bool):
        reject("Missing persisted statutory amount")

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        reject("Invalid persisted statutory amount")

    if not amount.is_finite():
        reject("Non-finite persisted statutory amount")

    if amount.as_tuple().exponent < -2:
        reject("Invalid persisted statutory precision")

    return amount


def compare_money(actual, expected, field):
    if money(actual) != money(expected):
        reject("Persisted statutory mismatch: " + field)


async def verify_persisted_payroll_sources(
    db,
    *,
    company_id,
    payroll_period_id,
    readiness,
):
    """Compare readiness against separately queried live ORM records.

    All current calculation IDs must match, and each identified source
    must match employee/contract/revision, frozen statutory totals,
    and each underlying statutory component line.
    """

    if (
        not isinstance(readiness, dict)
        or readiness.get("company_id") != company_id
        or readiness.get("payroll_period_id") != payroll_period_id
    ):
        reject("Readiness scope mismatch")

    sources = readiness.get("calculation_sources")

    if not isinstance(sources, list) or not sources:
        reject("Verified payroll sources required")

    expected = {}

    for source in sources:
        if not isinstance(source, dict):
            reject("Invalid readiness source")

        calculation_id = source.get("payroll_calculation_id")

        if (
            type(calculation_id) is not int
            or calculation_id <= 0
            or calculation_id in expected
        ):
            reject("Duplicate or invalid readiness calculation")

        expected[calculation_id] = source

    records = (
        await db.execute(
            select(
                PayrollCalculation,
                EmploymentContract,
                Employee,
                PayrollStatutoryResult,
            )
            .join(
                EmploymentContract,
                and_(
                    EmploymentContract.company_id
                    == PayrollCalculation.company_id,
                    EmploymentContract.id
                    == PayrollCalculation.employment_contract_id,
                ),
            )
            .join(
                Employee,
                and_(
                    Employee.company_id
                    == EmploymentContract.company_id,
                    Employee.id
                    == EmploymentContract.employee_id,
                ),
            )
            .outerjoin(
                PayrollStatutoryResult,
                and_(
                    PayrollStatutoryResult.company_id
                    == PayrollCalculation.company_id,
                    PayrollStatutoryResult.payroll_calculation_id
                    == PayrollCalculation.id,
                ),
            )
            .where(
                PayrollCalculation.company_id == company_id,
                PayrollCalculation.payroll_period_id
                == payroll_period_id,
                current_calculation_filter(),
            )
            .order_by(PayrollCalculation.id)
        )
    ).all()

    actual = {}

    for calculation, contract, employee, statutory in records:
        identifier = calculation.id

        if identifier in actual:
            reject("Duplicate persisted statutory calculation")

        if statutory is None:
            reject("Missing persisted statutory result")

        actual[identifier] = (
            calculation,
            contract,
            employee,
            statutory,
        )

    if set(expected) != set(actual):
        reject("Persisted payroll calculation set changed")

    statutory_ids = [
        record[3].id for record in actual.values()
    ]

    lines = (
        await db.scalars(
            select(PayrollStatutoryResultLine)
            .where(
                PayrollStatutoryResultLine.company_id == company_id,
                PayrollStatutoryResultLine.payroll_statutory_result_id
                .in_(statutory_ids),
            )
            .order_by(
                PayrollStatutoryResultLine.payroll_statutory_result_id,
                PayrollStatutoryResultLine.line_no,
            )
        )
    ).all()

    grouped = {}

    for line in lines:
        grouped.setdefault(
            line.payroll_statutory_result_id, []
        ).append(line)

    checked_line_ids = set()

    for identifier, source in expected.items():
        calculation, contract, employee, statutory = actual[identifier]

        scalar_fields = {
            "employee_id": employee.id,
            "employment_contract_id": contract.id,
            "revision": calculation.revision,
            "tax_number": employee.tax_number,
            "currency_code": statutory.currency_code,
        }

        for field, value in scalar_fields.items():
            if source.get(field) != value:
                reject(
                    "Persisted source identity mismatch: " + field
                )

        amounts = {
            "gross_amount": statutory.gross_amount,
            "net_amount": statutory.net_amount,
            "employee_withholding_amount":
                statutory.employee_withholding_amount,
            "employer_contribution_amount":
                statutory.employer_contribution_amount,
        }

        for field, value in amounts.items():
            compare_money(source.get(field), value, field)

        expected_lines = source.get("components")

        if not isinstance(expected_lines, list):
            reject("Invalid readiness component lines")

        indexed = {}

        for item in expected_lines:
            if not isinstance(item, dict):
                reject("Invalid statutory line projection")

            line_id = item.get("source_line_id")

            if (
                type(line_id) is not int
                or line_id <= 0
                or line_id in indexed
            ):
                reject("Duplicate readiness statutory line")

            if line_id in checked_line_ids:
                reject("Statutory line reused across calculations")

            indexed[line_id] = item
            checked_line_ids.add(line_id)

        live_lines = grouped.get(statutory.id, [])

        if set(indexed) != {line.id for line in live_lines}:
            reject("Persisted statutory component set changed")

        for line in live_lines:
            projected = indexed[line.id]

            if projected.get("component") != line.component:
                reject("Persisted statutory component mismatch")

            if projected.get("currency_code") != line.currency_code:
                reject("Persisted statutory component currency mismatch")

            for field in ("base_amount", "amount"):
                compare_money(
                    projected.get(field),
                    getattr(line, field),
                    field,
                )

            if money_rate(projected.get("rate")) != money_rate(line.rate):
                reject("Persisted statutory rate mismatch")

    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "source_count": len(actual),
        "component_count": len(checked_line_ids),
        "verified": True,
        "statutory_approved": False,
    }


def money_rate(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        reject("Invalid persisted statutory rate")

    if not result.is_finite():
        reject("Invalid persisted statutory rate")

    return result
