"""Atomic multi-calculation payroll posting.

This service creates separately traceable payroll disbursements
and posted journals in one database transaction.

It does not create a consolidated bank transfer, batch ledger,
or statutory form.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import PayrollCalculation
from app.models.employment_contract import EmploymentContract
from app.services.payroll_disbursement_service import (
    create_payroll_disbursement,
    generate_and_post_payroll_disbursement_journal,
)


class CrossCalculationPostingError(ValueError):
    pass


CENT = Decimal("0.01")


def validate_posting_items(items):
    """Validate amounts and source identifiers before DB writes."""

    if not isinstance(items, (list, tuple)) or len(items) < 2:
        raise CrossCalculationPostingError(
            "At least two payroll calculations are required"
        )

    seen = set()
    clean = []

    for item in items:
        if type(item) is not dict:
            raise CrossCalculationPostingError(
                "Invalid posting item"
            )

        calculation_id = item.get("payroll_calculation_id")

        if (
            type(calculation_id) is not int
            or calculation_id <= 0
            or calculation_id in seen
        ):
            raise CrossCalculationPostingError(
                "Invalid or duplicate payroll calculation"
            )

        seen.add(calculation_id)

        raw = item.get("amount")

        if type(raw) not in (str, Decimal):
            raise CrossCalculationPostingError(
                "Amount must be decimal money"
            )

        try:
            amount = Decimal(raw)
        except (InvalidOperation, ValueError):
            raise CrossCalculationPostingError(
                "Invalid monetary amount"
            ) from None

        if (
            not amount.is_finite()
            or amount <= 0
            or amount != amount.quantize(CENT)
        ):
            raise CrossCalculationPostingError(
                "Amount must be positive finite money"
            )

        clean.append((calculation_id, amount))

    return clean


async def post_cross_calculation_payments(
    db: AsyncSession,
    *,
    company_id: int,
    employee_id: int,
    bank_account_id: int | None,
    cash_desk_id: int | None,
    payment_date: date,
    created_by: int,
    request_key: str,
    items,
):
    """Atomically post several separately sourced payroll payouts.

    Caller owns the outer transaction and must commit it.
    An internal savepoint rolls back every new posting if any
    member fails. Concurrent calls for the same company and
    employee serialize through a PostgreSQL transaction lock.
    """

    for value, label in (
        (company_id, "company"),
        (employee_id, "employee"),
        (created_by, "actor"),
    ):
        if type(value) is not int or value <= 0:
            raise CrossCalculationPostingError(
                f"Invalid {label}"
            )

    if (bank_account_id is None) == (cash_desk_id is None):
        raise CrossCalculationPostingError(
            "Exactly one payment source is required"
        )

    if not isinstance(payment_date, date) or (
        type(payment_date) is not date
    ):
        raise CrossCalculationPostingError(
            "Valid payment date required"
        )

    if (
        type(request_key) is not str
        or not request_key.strip()
        or len(request_key) > 100
        or not request_key.isascii()
        or any(c.isspace() for c in request_key)
    ):
        raise CrossCalculationPostingError(
            "Invalid batch request key"
        )

    clean = validate_posting_items(items)

    # SAVEPOINT: failure of any component undoes all work
    # performed inside this service invocation.
    async with db.begin_nested():

        # Advisory transaction lock serializes attempts
        # for the same company and employee.
        await db.execute(
            text("""
                SELECT pg_advisory_xact_lock(
                    CAST(:company AS integer),
                    CAST(:employee AS integer)
                )
            """),
            {
                "company": company_id,
                "employee": employee_id,
            },
        )

        # Verify every calculation before creating payouts.
        for calculation_id, _ in clean:
            result = await db.execute(
                select(
                    PayrollCalculation,
                    EmploymentContract,
                )
                .join(
                    EmploymentContract,
                    EmploymentContract.id
                    == PayrollCalculation.employment_contract_id,
                )
                .where(
                    PayrollCalculation.company_id == company_id,
                    PayrollCalculation.id == calculation_id,
                    EmploymentContract.company_id == company_id,
                )
            )

            source = result.one_or_none()

            if source is None:
                raise CrossCalculationPostingError(
                    "Payroll calculation or contract unavailable"
                )

            calculation, contract = source

            if contract.employee_id != employee_id:
                raise CrossCalculationPostingError(
                    "Cross-employee payment is forbidden"
                )

            if calculation.currency_code != "UAH":
                raise CrossCalculationPostingError(
                    "Only UAH payroll sources are supported"
                )

        posted = []

        # Stable ordering reduces concurrent lock-order conflicts.
        for calculation_id, amount in sorted(clean):

            component_key = (
                f"crosscalc:{request_key}:{calculation_id}"
            )

            if len(component_key) > 200:
                raise CrossCalculationPostingError(
                    "Component request key too long"
                )

            payment = await create_payroll_disbursement(
                db,
                company_id=company_id,
                payroll_calculation_id=calculation_id,
                bank_account_id=bank_account_id,
                cash_desk_id=cash_desk_id,
                payment_date=payment_date,
                created_by=created_by,
                amount=amount,
                request_key=component_key,
            )

            journal = (
                await generate_and_post_payroll_disbursement_journal(
                    db,
                    company_id=company_id,
                    payroll_disbursement_id=payment.id,
                    created_by=created_by,
                )
            )

            posted.append({
                "payroll_calculation_id": calculation_id,
                "payroll_disbursement_id": payment.id,
                "journal_entry_id": journal.id,
                "amount": f"{amount:.2f}",
            })

        await db.flush()

    return {
        "company_id": company_id,
        "employee_id": employee_id,
        "request_key": request_key,
        "currency_code": "UAH",
        "payment_date": payment_date.isoformat(),
        "amount": format(
            sum((amount for _, amount in clean), Decimal("0.00")),
            ".2f",
        ),
        "components": posted,
        "consolidated_bank_transfer": False,
        "official_generation": "BLOCKED",
    }
