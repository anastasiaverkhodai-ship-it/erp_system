"""Register evidence-backed, previously posted advance tax payments.

No bank posting, payment execution, or automatic monthly allocation.
"""

from decimal import Decimal
from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.bank_account import BankAccount
from app.models.journal_entry import JournalEntry
from app.models.payroll_advance import PayrollAdvance
from app.models.payroll_advance_tax_remittance import (
    PayrollAdvanceTaxRemittance,
)
from app.models.payroll_tax_remittance import (
    PayrollTaxRemittance,
)
from app.services.payroll_advance_tax_source_proof import (
    verify_automatic_advance_tax_source_in_db,
)
from app.services.payroll_mutation_guard import (
    serialized_payroll_mutation,
)


class AdvanceTaxRemittanceError(ValueError):
    pass


COMPONENTS = frozenset({
    "personal_income_tax",
    "military_levy",
})


def _require(condition, message):
    if not condition:
        raise AdvanceTaxRemittanceError(message)


def _money(value):
    try:
        amount = Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise AdvanceTaxRemittanceError(
            "Invalid tax remittance amount"
        ) from exc

    _require(
        amount.is_finite()
        and amount > 0
        and amount.as_tuple().exponent >= -2,
        "Positive finite amount with at most two decimal places required",
    )
    return amount.quantize(Decimal("0.01"))


@serialized_payroll_mutation(AdvanceTaxRemittanceError)
async def register_advance_tax_remittance(
    db,
    *,
    company_id,
    payroll_advance_id,
    journal_entry_id,
    bank_account_id,
    component,
    amount,
    request_key,
    document_reference,
    budget_payment_reference,
    created_by,
):
    for name, value in (
        ("company_id", company_id),
        ("payroll_advance_id", payroll_advance_id),
        ("journal_entry_id", journal_entry_id),
        ("bank_account_id", bank_account_id),
        ("created_by", created_by),
    ):
        _require(
            type(value) is int and value > 0,
            "Invalid " + name,
        )

    _require(
        component in COMPONENTS,
        "Only PIT and military levy are supported",
    )

    for name, value, maximum in (
        ("request_key", request_key, 200),
        ("document_reference", document_reference, 500),
        ("budget_payment_reference", budget_payment_reference, 500),
    ):
        _require(
            isinstance(value, str)
            and bool(value.strip())
            and len(value.strip()) <= maximum,
            "Invalid " + name,
        )

    amount = _money(amount)
    request_key = request_key.strip()
    document_reference = document_reference.strip()
    budget_payment_reference = budget_payment_reference.strip()

    payload = {
        "company_id": company_id,
        "payroll_advance_id": payroll_advance_id,
        "journal_entry_id": journal_entry_id,
        "bank_account_id": bank_account_id,
        "component": component,
        "amount": str(amount),
        "request_key": request_key,
        "document_reference": document_reference,
        "budget_payment_reference": budget_payment_reference,
    }
    fingerprint = sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()

    old = await db.scalar(
        select(PayrollAdvanceTaxRemittance).where(
            PayrollAdvanceTaxRemittance.company_id == company_id,
            PayrollAdvanceTaxRemittance.request_key == request_key,
        )
    )

    if old is not None:
        _require(
            old.request_fingerprint == fingerprint,
            "Request key has different financial content",
        )
        return old

    advance = await db.scalar(
        select(PayrollAdvance).where(
            PayrollAdvance.company_id == company_id,
            PayrollAdvance.id == payroll_advance_id,
        ).with_for_update()
    )
    _require(advance is not None, "Payroll advance not found")
    _require(
        advance.currency_code == "UAH",
        "Advance must use UAH",
    )

    proof = await verify_automatic_advance_tax_source_in_db(
        db, advance
    )
    _require(
        proof.advance_id == advance.id
        and proof.company_id == company_id,
        "Advance tax proof mismatch",
    )

    matching = [
        row for row in proof.components
        if row.component == component
    ]
    _require(
        len(matching) == 1
        and _money(matching[0].amount) == amount,
        "Payment must equal verified advance tax component",
    )

    original_advance_journal = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.payroll_advance_id == payroll_advance_id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )
    _require(
        original_advance_journal is not None
        and original_advance_journal.status == "posted"
        and original_advance_journal.entry_date
            == advance.payment_date,
        "Original advance bank posting required",
    )

    payment = await db.scalar(
        select(JournalEntry)
        .options(selectinload(JournalEntry.lines))
        .where(
            JournalEntry.company_id == company_id,
            JournalEntry.id == journal_entry_id,
        ).with_for_update()
    )
    from app.models.payroll_sick_return import PayrollSickReturn
    if await db.scalar(select(PayrollSickReturn.id).where(
        PayrollSickReturn.company_id==company_id,
        PayrollSickReturn.journal_entry_id==journal_entry_id)) is not None:
        raise AdvanceTaxRemittanceError('Bank journal already attributed to a sick correction return')
    _require(
        payment is not None
        and payment.status == "posted"
        and payment.reversal_of_id is None
        and payment.entry_date == advance.payment_date,
        "Original posted same-day tax payment required",
    )

    _require(
        all(
            getattr(payment, column.name) is None
            for column in JournalEntry.__table__.columns
            if column.name.endswith("_id")
            and column.name not in {
                "id", "company_id", "reversal_of_id",
                "accounting_rule_id",
            }
        ),
        "Tax payment belongs to another financial source",
    )

    previously_allocated = await db.scalar(
        select(PayrollTaxRemittance.id).where(
            PayrollTaxRemittance.company_id == company_id,
            PayrollTaxRemittance.journal_entry_id == journal_entry_id,
        )
    )
    _require(
        previously_allocated is None,
        "Bank journal already allocated to monthly tax",
    )

    duplicate = await db.scalar(
        select(PayrollAdvanceTaxRemittance.id).where(
            PayrollAdvanceTaxRemittance.company_id == company_id,
            (
                PayrollAdvanceTaxRemittance.journal_entry_id
                == journal_entry_id
            )
            | (
                (
                    PayrollAdvanceTaxRemittance.payroll_advance_id
                    == payroll_advance_id
                )
                & (
                    PayrollAdvanceTaxRemittance.component
                    == component
                )
            ),
        )
    )
    _require(
        duplicate is None,
        "Advance tax already attributed",
    )

    bank = await db.scalar(
        select(BankAccount).where(
            BankAccount.company_id == company_id,
            BankAccount.id == bank_account_id,
            BankAccount.is_active.is_(True),
        )
    )
    _require(
        bank is not None
        and bank.currency_code == "UAH"
        and advance.bank_account_id == bank.id,
        "Active advance-linked UAH bank account required",
    )

    bank_gl = await db.scalar(
        select(Account).where(
            Account.id == bank.accounting_account_id,
            Account.company_id == company_id,
        )
    )
    _require(
        bank_gl is not None
        and bank_gl.account_type == "asset"
        and bank_gl.code.startswith("31"),
        "Verified bank GL account required",
    )

    from app.services.payroll_tax_remittance_service import ROLES
    from app.services.accounting_account_role_resolver import (
        resolve_company_account_roles,
    )

    _require(component in ROLES, "Unsupported tax account role")

    accounts = await resolve_company_account_roles(
        db,
        company_id=company_id,
        roles=(ROLES[component],),
    )
    liability_gl = accounts[ROLES[component]]

    _require(
        liability_gl.id != bank_gl.id,
        "Bank and tax payable must be different accounts",
    )

    debit = Decimal("0.00")
    credit = Decimal("0.00")

    _require(
        len(payment.lines) == 2,
        "Exactly two tax payment journal lines required",
    )

    for line in payment.lines:
        line_debit = Decimal(line.debit)
        line_credit = Decimal(line.credit)

        if line.account_id == liability_gl.id:
            _require(
                line_debit == amount
                and line_credit == Decimal("0.00"),
                "Tax payable debit does not match advance tax",
            )
            debit += line_debit
        elif line.account_id == bank_gl.id:
            _require(
                line_credit == amount
                and line_debit == Decimal("0.00"),
                "Bank credit does not match advance tax",
            )
            credit += line_credit
        else:
            raise AdvanceTaxRemittanceError(
                "Unexpected account in tax payment journal"
            )

    _require(
        debit == amount and credit == amount,
        "Unbalanced or incorrect tax bank payment",
    )

    row = PayrollAdvanceTaxRemittance(
        company_id=company_id,
        payroll_advance_id=payroll_advance_id,
        journal_entry_id=journal_entry_id,
        bank_account_id=bank_account_id,
        component=component,
        amount=amount,
        request_key=request_key,
        request_fingerprint=fingerprint,
        document_reference=document_reference,
        budget_payment_reference=budget_payment_reference,
        created_by=created_by,
    )

    db.add(row)
    await db.flush()
    return row


@serialized_payroll_mutation(AdvanceTaxRemittanceError)
async def reverse_advance_tax_remittance(
    db,
    *,
    company_id,
    remittance_id,
    reversal_date,
    reversed_by,
):
    """Reverse the original posted advance-tax bank journal.

    Retain the original attribution for dated historical queries.
    """
    from datetime import date
    from app.services.accounting_reversal import reverse_journal_entry

    for name, value in (
        ("company_id", company_id),
        ("remittance_id", remittance_id),
        ("reversed_by", reversed_by),
    ):
        _require(
            type(value) is int and value > 0,
            "Invalid " + name,
        )

    _require(
        type(reversal_date) is date,
        "Valid reversal date required",
    )

    row = await db.scalar(
        select(PayrollAdvanceTaxRemittance).where(
            PayrollAdvanceTaxRemittance.company_id == company_id,
            PayrollAdvanceTaxRemittance.id == remittance_id,
        )
    )
    _require(
        row is not None,
        "Company-scoped advance tax remittance not found",
    )

    original = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.id == row.journal_entry_id,
        )
    )
    _require(
        original is not None
        and original.reversal_of_id is None
        and original.status in ("posted", "reversed"),
        "Original advance tax payment missing",
    )
    _require(
        reversal_date >= original.entry_date,
        "Cannot reverse advance tax payment before posting date",
    )

    existing = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.reversal_of_id == original.id,
        )
    )

    if existing is not None:
        _require(
            existing.status == "posted"
            and existing.entry_date == reversal_date,
            "Existing reversal date or state differs",
        )
        return row

    _require(
        original.status == "posted",
        "Original advance tax payment is not active",
    )

    await reverse_journal_entry(
        db,
        company_id,
        original.id,
        reversal_date,
        reversed_by,
    )

    return row
