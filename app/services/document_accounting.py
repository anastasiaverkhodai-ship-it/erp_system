from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accounting_rule import AccountingRule
from app.models.accounting_rule_line import (
    AccountingAmountSource,
    AccountingRuleSide,
)
from app.models.document import (
    Document,
    DocumentStatus,
)
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)

from app.models.inventory_cost_entry import InventoryCostEntry
from app.models.journal_entry_line import JournalEntryLine
from app.services.accounting_posting import (
    AccountingPostingError,
    post_journal_entry,
    validate_journal_entry,
)


class DocumentAccountingError(Exception):
    pass


class DocumentAccountingNotFoundError(
    DocumentAccountingError
):
    pass


def _money(
    value: Decimal,
) -> Decimal:
    return value.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


async def generate_journal_entry_from_document(
    db: AsyncSession,
    company_id: int,
    document_id: int,
    accounting_rule_id: int,
    created_by: int,
) -> JournalEntry:
    # --------------------------------------------------
    # 1. Load and lock the source document
    # --------------------------------------------------

    document_result = await db.execute(
        select(Document)
        .options(
            selectinload(Document.lines)
        )
        .where(
            Document.id == document_id,
            Document.company_id == company_id,
        )
        .with_for_update()
    )

    document = document_result.scalar_one_or_none()

    if document is None:
        raise DocumentAccountingNotFoundError(
            "Document not found"
        )

    if document.status != DocumentStatus.POSTED:
        raise DocumentAccountingError(
            "Only posted documents can generate accounting entries"
        )

    if not document.lines:
        raise DocumentAccountingError(
            "Document has no lines"
        )

    # --------------------------------------------------
    # 2. Load and lock the accounting rule
    # --------------------------------------------------

    rule_result = await db.execute(
        select(AccountingRule)
        .options(
            selectinload(AccountingRule.lines)
        )
        .where(
            AccountingRule.id == accounting_rule_id,
            AccountingRule.company_id == company_id,
        )
        .with_for_update()
    )

    accounting_rule = (
        rule_result.scalar_one_or_none()
    )

    if accounting_rule is None:
        raise DocumentAccountingNotFoundError(
            "Accounting rule not found"
        )

    if not accounting_rule.is_active:
        raise DocumentAccountingError(
            "Accounting rule is inactive"
        )

    if (
        accounting_rule.document_type
        != document.document_type
    ):
        raise DocumentAccountingError(
            (
                "Accounting rule document type does not "
                "match the document type"
            )
        )

    if not accounting_rule.lines:
        raise DocumentAccountingError(
            "Accounting rule has no lines"
        )

    # --------------------------------------------------
    # 3. Prevent duplicate accounting generation
    # --------------------------------------------------

    existing_result = await db.execute(
        select(JournalEntry.id).where(
            JournalEntry.company_id == company_id,
            JournalEntry.document_id == document.id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )

    if (
        existing_result.scalar_one_or_none()
        is not None
    ):
        raise DocumentAccountingError(
            (
                "Accounting journal entry already exists "
                "for this document"
            )
        )

    # --------------------------------------------------
    # 4. Calculate monetary amounts
    # --------------------------------------------------

    document_line_amounts = {
        line.id: _money(
            line.quantity * line.price
        )
        for line in document.lines
    }

    document_total = sum(
        document_line_amounts.values(),
        Decimal("0.00"),
    )

    inventory_cost_entries_result = await db.execute(
        select(InventoryCostEntry)
        .where(
            InventoryCostEntry.company_id == company_id,
            InventoryCostEntry.document_id == document.id,
        )
        .order_by(
            InventoryCostEntry.document_line_id
        )
    )

    inventory_cost_entries = (
        inventory_cost_entries_result.scalars().all()
    )

    inventory_cost_by_line = {
        entry.document_line_id: entry
        for entry in inventory_cost_entries
    }

    # --------------------------------------------------
    # 5. Generate JournalEntry lines
    # --------------------------------------------------

    generated_lines: list[JournalEntryLine] = []

    line_no = 1

    for rule_line in sorted(
        accounting_rule.lines,
        key=lambda item: item.line_no,
    ):
        amounts: list[Decimal]

        if (
            rule_line.amount_source
            == AccountingAmountSource.DOCUMENT_TOTAL
        ):
            amounts = [
                document_total
            ]

        elif (
            rule_line.amount_source
            == AccountingAmountSource.LINE_TOTAL
        ):
            amounts = [
                document_line_amounts[
                    document_line.id
                ]
                for document_line in sorted(
                    document.lines,
                    key=lambda item: item.id,
                )
            ]

        elif (
            rule_line.amount_source
            == AccountingAmountSource.INVENTORY_COST
        ):
            inventory_cost_amounts: list[Decimal] = []

            for document_line in sorted(
                document.lines,
                key=lambda item: item.id,
            ):
                cost_entry = inventory_cost_by_line.get(
                    document_line.id
                )

                if cost_entry is None:
                    raise DocumentAccountingError(
                        (
                            "Inventory cost was not found "
                            f"for document line "
                            f"{document_line.id}"
                        )
                    )

                if (
                    Decimal(cost_entry.quantity)
                    != Decimal(document_line.quantity)
                ):
                    raise DocumentAccountingError(
                        (
                            "Inventory cost quantity does "
                            "not match document line "
                            f"{document_line.id}. "
                            f"Costed: {cost_entry.quantity}, "
                            f"required: "
                            f"{document_line.quantity}"
                        )
                    )

                inventory_cost_amounts.append(
                    _money(
                        Decimal(
                            cost_entry.cost_amount
                        )
                    )
                )

            amounts = inventory_cost_amounts

        else:
            raise DocumentAccountingError(
                (
                    "Unsupported accounting "
                    "amount source"
                )
            )

        for amount in amounts:
            if amount <= 0:
                raise DocumentAccountingError(
                    (
                        "Generated accounting amount "
                        "must be greater than zero"
                    )
                )

            if (
                rule_line.side
                == AccountingRuleSide.DEBIT
            ):
                debit = amount
                credit = Decimal("0.00")

            elif (
                rule_line.side
                == AccountingRuleSide.CREDIT
            ):
                debit = Decimal("0.00")
                credit = amount

            else:
                raise DocumentAccountingError(
                    "Unsupported accounting side"
                )

            generated_lines.append(
                JournalEntryLine(
                    line_no=line_no,
                    account_id=rule_line.account_id,
                    debit=debit,
                    credit=credit,
                    description=rule_line.description,
                )
            )

            line_no += 1

    # --------------------------------------------------
    # 6. Create DRAFT JournalEntry
    # --------------------------------------------------

    if (
        document.accounting_rule_id is not None
        and document.accounting_rule_id
        != accounting_rule.id
    ):
        raise DocumentAccountingError(
            "Document is already linked to a different "
            "accounting rule"
        )

    document.accounting_rule_id = accounting_rule.id

    journal_entry = JournalEntry(
        company_id=company_id,
        document_id=document.id,
        accounting_rule_id=accounting_rule.id,
        entry_date=document.document_date,
        description=(
            f"Generated from Document "
            f"{document.number} using "
            f"{accounting_rule.code}"
        ),
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )

    journal_entry.lines = generated_lines

    # --------------------------------------------------
    # 7. Validate generated accounting entry
    # --------------------------------------------------

    try:
        await validate_journal_entry(
            db=db,
            journal_entry=journal_entry,
        )

    except AccountingPostingError as exc:
        raise DocumentAccountingError(
            str(exc)
        ) from exc

    db.add(journal_entry)

    await db.flush()

    return journal_entry


async def generate_and_post_journal_entry_from_document(
    db: AsyncSession,
    company_id: int,
    document_id: int,
    accounting_rule_id: int,
    created_by: int,
) -> JournalEntry:
    journal_entry = (
        await generate_journal_entry_from_document(
            db=db,
            company_id=company_id,
            document_id=document_id,
            accounting_rule_id=accounting_rule_id,
            created_by=created_by,
        )
    )

    try:
        posted_entry = await post_journal_entry(
            db=db,
            company_id=company_id,
            journal_entry_id=journal_entry.id,
        )

    except AccountingPostingError as exc:
        raise DocumentAccountingError(
            str(exc)
        ) from exc

    return posted_entry