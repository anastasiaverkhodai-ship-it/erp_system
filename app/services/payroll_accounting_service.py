from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import JournalEntryLine
from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationStatus,
    PayrollPeriod,
)
from app.models.payroll_statutory import (
    PayrollStatutoryComponent,
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)
from app.services.accounting_account_role_resolver import (
    AccountingAccountRoleResolutionError,
    resolve_company_account_roles,
)
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.accounting_posting import (
    AccountingPostingError,
    post_journal_entry,
    validate_journal_entry,
)
from app.services.accounting_reversal import (
    AccountingReversalError,
    reverse_journal_entry,
)


ZERO = Decimal("0.00")


class PayrollAccountingError(Exception):
    pass


class PayrollAccountingNotFoundError(
    PayrollAccountingError
):
    pass


class PayrollAccountingSourceStateError(
    PayrollAccountingError
):
    pass


class PayrollAccountingDuplicateError(
    PayrollAccountingError
):
    pass


class PayrollAccountingCurrencyError(
    PayrollAccountingError
):
    pass


class PayrollAccountingReconciliationError(
    PayrollAccountingError
):
    pass


def _role(*candidates: str) -> AccountingAccountRole:
    for name in candidates:
        value = getattr(
            AccountingAccountRole,
            name,
            None,
        )
        if value is not None:
            return value

    raise PayrollAccountingError(
        "Required payroll accounting role is absent: "
        + ", ".join(candidates)
    )


def _payroll_roles() -> dict[str, AccountingAccountRole]:
    return {
        "gross_expense": _role(
            "PAYROLL_GROSS_EXPENSE",
            "PAYROLL_EXPENSE",
            "SALARY_EXPENSE",
        ),
        "net_payable": _role(
            "PAYROLL_NET_PAYABLE",
            "PAYROLL_PAYABLE",
            "SALARY_PAYABLE",
        ),
        "pit_payable": _role(
            "PAYROLL_PIT_PAYABLE",
            "PIT_PAYABLE",
            "PERSONAL_INCOME_TAX_PAYABLE",
        ),
        "military_payable": _role(
            "PAYROLL_MILITARY_LEVY_PAYABLE",
            "MILITARY_LEVY_PAYABLE",
        ),
        "usc_expense": _role(
            "PAYROLL_EMPLOYER_CONTRIBUTION_EXPENSE",
        ),
        "usc_payable": _role(
            "PAYROLL_USC_PAYABLE",
            "USC_PAYABLE",
            "UNIFIED_SOCIAL_CONTRIBUTION_PAYABLE",
        ),
    }


async def _load_sources(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> tuple[
    PayrollCalculation,
    PayrollPeriod,
    PayrollStatutoryResult,
    list[PayrollStatutoryResultLine],
]:
    calculation = (
        await db.execute(
            select(PayrollCalculation)
            .where(
                PayrollCalculation.company_id
                == company_id,
                PayrollCalculation.id
                == payroll_calculation_id,
            )
        )
    ).scalar_one_or_none()

    if calculation is None:
        raise PayrollAccountingNotFoundError(
            "Payroll calculation not found"
        )

    period = (
        await db.execute(
            select(PayrollPeriod).where(
                PayrollPeriod.company_id
                == company_id,
                PayrollPeriod.id
                == calculation.payroll_period_id,
            )
        )
    ).scalar_one_or_none()

    if period is None:
        raise PayrollAccountingSourceStateError(
            "Payroll period not found"
        )

    statutory = (
        await db.execute(
            select(PayrollStatutoryResult).where(
                PayrollStatutoryResult.company_id
                == company_id,
                PayrollStatutoryResult.payroll_calculation_id
                == calculation.id,
            )
        )
    ).scalar_one_or_none()

    if statutory is None:
        raise PayrollAccountingSourceStateError(
            "Payroll statutory result is required "
            "before accounting posting"
        )

    statutory_lines = list(
        (
            await db.execute(
                select(PayrollStatutoryResultLine)
                .where(
                    PayrollStatutoryResultLine.company_id
                    == company_id,
                    PayrollStatutoryResultLine
                    .payroll_statutory_result_id
                    == statutory.id,
                )
                .order_by(
                    PayrollStatutoryResultLine.line_no.asc()
                )
            )
        ).scalars().all()
    )

    if not statutory_lines:
        raise PayrollAccountingSourceStateError(
            "Payroll statutory result lines are required"
        )

    return (
        calculation,
        period,
        statutory,
        statutory_lines,
    )


def _component_amounts(
    lines: list[PayrollStatutoryResultLine],
) -> dict[str, Decimal]:
    amounts = {
        PayrollStatutoryComponent
        .PERSONAL_INCOME_TAX.value: ZERO,
        PayrollStatutoryComponent
        .MILITARY_LEVY.value: ZERO,
        PayrollStatutoryComponent
        .UNIFIED_SOCIAL_CONTRIBUTION.value: ZERO,
    }

    for line in lines:
        if line.component not in amounts:
            raise PayrollAccountingSourceStateError(
                "Unsupported payroll statutory component: "
                f"{line.component}"
            )

        amounts[line.component] += Decimal(
            line.amount
        )

    return amounts


def _validate_source_reconciliation(
    *,
    calculation: PayrollCalculation,
    statutory: PayrollStatutoryResult,
    components: dict[str, Decimal],
) -> None:
    gross = Decimal(calculation.gross_amount)
    statutory_gross = Decimal(statutory.gross_amount)
    withholding = Decimal(
        statutory.employee_withholding_amount
    )
    employer = Decimal(
        statutory.employer_contribution_amount
    )
    net = Decimal(statutory.net_amount)

    pit = components[
        PayrollStatutoryComponent
        .PERSONAL_INCOME_TAX.value
    ]
    military = components[
        PayrollStatutoryComponent
        .MILITARY_LEVY.value
    ]
    usc = components[
        PayrollStatutoryComponent
        .UNIFIED_SOCIAL_CONTRIBUTION.value
    ]

    if gross != statutory_gross:
        raise PayrollAccountingReconciliationError(
            "Payroll gross does not match statutory gross"
        )

    if withholding != pit + military:
        raise PayrollAccountingReconciliationError(
            "Employee withholding does not reconcile "
            "to statutory result lines"
        )

    if employer != usc:
        raise PayrollAccountingReconciliationError(
            "Employer contribution does not reconcile "
            "to statutory result lines"
        )

    if net != gross - withholding:
        raise PayrollAccountingReconciliationError(
            "Payroll net does not reconcile"
        )

    debit = gross + employer
    credit = net + pit + military + usc

    if debit != credit:
        raise PayrollAccountingReconciliationError(
            "Payroll accounting plan is not balanced"
        )


async def get_payroll_accounting_journal(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> JournalEntry | None:
    return (
        await db.execute(
            select(JournalEntry)
            .options(
                selectinload(JournalEntry.lines)
            )
            .where(
                JournalEntry.company_id
                == company_id,
                JournalEntry.payroll_calculation_id
                == payroll_calculation_id,
                JournalEntry.reversal_of_id.is_(None),
            )
        )
    ).scalar_one_or_none()


async def generate_and_post_payroll_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
    created_by: int,
) -> JournalEntry:
    existing = await get_payroll_accounting_journal(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    if existing is not None:
        return existing

    (
        calculation,
        period,
        statutory,
        statutory_lines,
    ) = await _load_sources(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    if calculation.currency_code != "UAH":
        raise PayrollAccountingCurrencyError(
            "Payroll accounting currently supports "
            "UAH only"
        )

    if statutory.currency_code != calculation.currency_code:
        raise PayrollAccountingSourceStateError(
            "Payroll statutory currency does not match "
            "payroll calculation currency"
        )

    components = _component_amounts(
        statutory_lines
    )

    _validate_source_reconciliation(
        calculation=calculation,
        statutory=statutory,
        components=components,
    )

    roles = _payroll_roles()

    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=tuple(roles.values()),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise PayrollAccountingError(
            str(exc)
        ) from exc

    gross = Decimal(calculation.gross_amount)
    net = Decimal(statutory.net_amount)

    pit = components[
        PayrollStatutoryComponent
        .PERSONAL_INCOME_TAX.value
    ]
    military = components[
        PayrollStatutoryComponent
        .MILITARY_LEVY.value
    ]
    usc = components[
        PayrollStatutoryComponent
        .UNIFIED_SOCIAL_CONTRIBUTION.value
    ]

    journal = JournalEntry(
        company_id=company_id,
        payroll_calculation_id=calculation.id,
        entry_date=period.end_date,
        description=(
            "Payroll accounting for payroll calculation "
            f"{calculation.id}"
        ),
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )

    payload = [
        (
            roles["gross_expense"],
            gross,
            ZERO,
            "Payroll gross expense",
        ),
        (
            roles["usc_expense"],
            usc,
            ZERO,
            "Employer unified social contribution expense",
        ),
        (
            roles["net_payable"],
            ZERO,
            net,
            "Payroll net payable",
        ),
        (
            roles["pit_payable"],
            ZERO,
            pit,
            "Personal income tax payable",
        ),
        (
            roles["military_payable"],
            ZERO,
            military,
            "Military levy payable",
        ),
        (
            roles["usc_payable"],
            ZERO,
            usc,
            "Unified social contribution payable",
        ),
    ]

    journal.lines = [
        JournalEntryLine(
            line_no=line_no,
            account_id=accounts[role].id,
            debit=debit,
            credit=credit,
            description=description,
        )
        for line_no, (
            role,
            debit,
            credit,
            description,
        ) in enumerate(
            (
                row
                for row in payload
                if row[1] != ZERO
                or row[2] != ZERO
            ),
            start=1,
        )
    ]

    db.add(journal)
    await db.flush()

    try:
        await validate_journal_entry(
            db,
            journal,
        )
        return await post_journal_entry(
            db,
            company_id,
            journal.id,
        )
    except AccountingPostingError as exc:
        raise PayrollAccountingError(
            str(exc)
        ) from exc


async def reverse_payroll_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
    reversal_date: date,
    reversed_by: int,
) -> JournalEntry:
    original = await get_payroll_accounting_journal(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    if original is None:
        raise PayrollAccountingNotFoundError(
            "Original payroll JournalEntry not found"
        )

    try:
        return await reverse_journal_entry(
            db,
            company_id,
            original.id,
            reversal_date,
            reversed_by,
        )
    except AccountingReversalError as exc:
        raise PayrollAccountingError(
            str(exc)
        ) from exc
