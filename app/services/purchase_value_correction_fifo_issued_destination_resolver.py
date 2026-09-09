from dataclasses import dataclass
from decimal import Decimal

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
    DocumentType,
)
from app.models.document_line import DocumentLine
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.purchase_value_correction_fifo_impact_event import (
    PurchaseValueCorrectionFifoImpactEvent,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionFifoIssuedDestinationError(
    Exception
):
    """Base historical issued-destination resolution error."""


class PurchaseValueCorrectionFifoIssuedDestinationSourceError(
    PurchaseValueCorrectionFifoIssuedDestinationError
):
    """FIFO impact ISSUE provenance is invalid."""


class PurchaseValueCorrectionFifoIssuedDestinationJournalError(
    PurchaseValueCorrectionFifoIssuedDestinationError
):
    """Historical ISSUE JournalEntry provenance is invalid."""


class PurchaseValueCorrectionFifoIssuedDestinationRuleError(
    PurchaseValueCorrectionFifoIssuedDestinationError
):
    """Historical ISSUE accounting-rule provenance is ambiguous."""


@dataclass(
    frozen=True,
    slots=True,
)
class HistoricalIssuedDestination:
    account_id: int
    original_journal_entry_id: int
    accounting_rule_id: int


def _positive_id(
    value,
    *,
    field: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            f"{field} must be a positive integer"
        )

    return value


def _money(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(value or ZERO)
        )
    except Exception as exc:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            f"{field} must be Decimal-compatible"
        ) from exc

    if not result.is_finite():
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            f"{field} must be finite"
        )

    return result


def resolve_historical_issued_destination_from_context(
    *,
    event: PurchaseValueCorrectionFifoImpactEvent,
    issue_document: Document,
    issue_line: DocumentLine,
    journal_entry: JournalEntry,
    accounting_rule: AccountingRule,
) -> HistoricalIssuedDestination:
    """
    Resolve the exact historical debit destination of an ISSUE cost.

    The destination is not inferred from the current GOODS_COGS role.
    It must be supported by BOTH:

        1. the accounting rule attached to the original ISSUE; and
        2. the actual original posted JournalEntry.

    This deliberately fails closed if the accounting rule and historical
    journal no longer describe the same cost destination.
    """

    if event.destination_kind != "issued":
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            "Historical issued destination requires destination_kind=issued"
        )

    company_id = _positive_id(
        event.company_id,
        field="event.company_id",
    )

    issue_document_id = _positive_id(
        event.issue_document_id,
        field="event.issue_document_id",
    )

    issue_document_line_id = _positive_id(
        event.issue_document_line_id,
        field="event.issue_document_line_id",
    )

    _positive_id(
        event.stock_lot_consumption_id,
        field="event.stock_lot_consumption_id",
    )

    if issue_document.id != issue_document_id:
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            "FIFO impact ISSUE document identity mismatch"
        )

    if issue_document.company_id != company_id:
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            "FIFO impact ISSUE document company mismatch"
        )

    if issue_document.document_type != DocumentType.ISSUE:
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            "FIFO impact historical document is not ISSUE"
        )

    if issue_document.status != DocumentStatus.POSTED:
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            "FIFO impact historical ISSUE is not POSTED"
        )

    if issue_line.id != issue_document_line_id:
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            "FIFO impact ISSUE line identity mismatch"
        )

    if issue_line.document_id != issue_document_id:
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            "FIFO impact ISSUE line does not belong to ISSUE document"
        )

    if event.recognition_date < issue_document.document_date:
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            "FIFO impact recognition date predates ISSUE document"
        )

    accounting_rule_id = _positive_id(
        issue_document.accounting_rule_id,
        field="issue_document.accounting_rule_id",
    )

    journal_entry_id = _positive_id(
        journal_entry.id,
        field="journal_entry.id",
    )

    if journal_entry.company_id != company_id:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Historical ISSUE JournalEntry company mismatch"
        )

    if journal_entry.document_id != issue_document_id:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Historical JournalEntry does not belong to ISSUE document"
        )

    if journal_entry.reversal_of_id is not None:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Historical ISSUE cost destination cannot use reversal JournalEntry"
        )

    if journal_entry.status != JournalEntryStatus.POSTED:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Historical ISSUE JournalEntry is not POSTED"
        )

    if journal_entry.accounting_rule_id != accounting_rule_id:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Historical ISSUE JournalEntry accounting rule mismatch"
        )

    if journal_entry.entry_date > event.recognition_date:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Historical ISSUE JournalEntry postdates FIFO impact"
        )

    if accounting_rule.id != accounting_rule_id:
        raise PurchaseValueCorrectionFifoIssuedDestinationRuleError(
            "Historical accounting rule identity mismatch"
        )

    if accounting_rule.company_id != company_id:
        raise PurchaseValueCorrectionFifoIssuedDestinationRuleError(
            "Historical accounting rule company mismatch"
        )

    if accounting_rule.document_type != DocumentType.ISSUE:
        raise PurchaseValueCorrectionFifoIssuedDestinationRuleError(
            "Historical accounting rule is not an ISSUE rule"
        )

    rule_lines = tuple(
        accounting_rule.lines
    )

    if not rule_lines:
        raise PurchaseValueCorrectionFifoIssuedDestinationRuleError(
            "Historical ISSUE accounting rule has no lines"
        )

    inventory_debit_lines = tuple(
        line
        for line in rule_lines
        if (
            line.amount_source
            == AccountingAmountSource.INVENTORY_COST
            and line.side
            == AccountingRuleSide.DEBIT
        )
    )

    inventory_credit_lines = tuple(
        line
        for line in rule_lines
        if (
            line.amount_source
            == AccountingAmountSource.INVENTORY_COST
            and line.side
            == AccountingRuleSide.CREDIT
        )
    )

    if len(inventory_debit_lines) != 1:
        raise PurchaseValueCorrectionFifoIssuedDestinationRuleError(
            "Historical ISSUE rule must expose exactly one "
            "INVENTORY_COST debit destination"
        )

    if not inventory_credit_lines:
        raise PurchaseValueCorrectionFifoIssuedDestinationRuleError(
            "Historical ISSUE rule has no INVENTORY_COST credit side"
        )

    destination_account_id = _positive_id(
        inventory_debit_lines[0].account_id,
        field="historical destination account_id",
    )

    credit_account_ids = {
        _positive_id(
            line.account_id,
            field="historical inventory credit account_id",
        )
        for line in inventory_credit_lines
    }

    if destination_account_id in credit_account_ids:
        raise PurchaseValueCorrectionFifoIssuedDestinationRuleError(
            "Historical ISSUE destination and inventory credit "
            "account cannot be the same"
        )

    journal_lines = tuple(
        journal_entry.lines
    )

    if not journal_lines:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Historical ISSUE JournalEntry has no lines"
        )

    rule_account_ids = {
        _positive_id(
            line.account_id,
            field="accounting rule account_id",
        )
        for line in rule_lines
    }

    journal_account_ids = {
        _positive_id(
            line.account_id,
            field="JournalEntryLine.account_id",
        )
        for line in journal_lines
    }

    if journal_account_ids != rule_account_ids:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Historical ISSUE JournalEntry accounts no longer "
            "match its accounting rule"
        )

    destination_debits = tuple(
        line
        for line in journal_lines
        if (
            line.account_id == destination_account_id
            and _money(
                line.debit,
                field="historical destination debit",
            )
            > ZERO
            and _money(
                line.credit,
                field="historical destination credit",
            )
            == ZERO
        )
    )

    if not destination_debits:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Historical ISSUE JournalEntry does not contain "
            "the rule-defined INVENTORY_COST debit destination"
        )

    if any(
        (
            line.account_id == destination_account_id
            and _money(
                line.credit,
                field="historical destination credit",
            )
            > ZERO
        )
        for line in journal_lines
    ):
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Historical ISSUE destination account appears on credit side"
        )

    has_inventory_credit = any(
        (
            line.account_id in credit_account_ids
            and _money(
                line.credit,
                field="historical inventory credit",
            )
            > ZERO
            and _money(
                line.debit,
                field="historical inventory debit",
            )
            == ZERO
        )
        for line in journal_lines
    )

    if not has_inventory_credit:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Historical ISSUE JournalEntry does not contain "
            "its INVENTORY_COST credit side"
        )

    return HistoricalIssuedDestination(
        account_id=destination_account_id,
        original_journal_entry_id=journal_entry_id,
        accounting_rule_id=accounting_rule_id,
    )


async def resolve_purchase_value_correction_fifo_issued_destination(
    db: AsyncSession,
    *,
    event: PurchaseValueCorrectionFifoImpactEvent,
    lock: bool = False,
) -> HistoricalIssuedDestination:
    if event.destination_kind != "issued":
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            "Historical destination resolver accepts issued impacts only"
        )

    company_id = _positive_id(
        event.company_id,
        field="event.company_id",
    )

    issue_document_id = _positive_id(
        event.issue_document_id,
        field="event.issue_document_id",
    )

    issue_line_id = _positive_id(
        event.issue_document_line_id,
        field="event.issue_document_line_id",
    )

    document_statement = select(
        Document
    ).where(
        Document.id == issue_document_id,
        Document.company_id == company_id,
    )

    if lock:
        document_statement = (
            document_statement.with_for_update()
        )

    issue_document = (
        await db.execute(
            document_statement
        )
    ).scalar_one_or_none()

    if issue_document is None:
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            "Historical ISSUE document was not found"
        )

    issue_line = (
        await db.execute(
            select(
                DocumentLine
            ).where(
                DocumentLine.id == issue_line_id,
                DocumentLine.document_id == issue_document_id,
            )
        )
    ).scalar_one_or_none()

    if issue_line is None:
        raise PurchaseValueCorrectionFifoIssuedDestinationSourceError(
            "Historical ISSUE document line was not found"
        )

    journal_statement = (
        select(
            JournalEntry
        )
        .options(
            selectinload(
                JournalEntry.lines
            )
        )
        .where(
            JournalEntry.company_id == company_id,
            JournalEntry.document_id == issue_document_id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )

    if lock:
        journal_statement = (
            journal_statement.with_for_update()
        )

    journal_entry = (
        await db.execute(
            journal_statement
        )
    ).scalar_one_or_none()

    if journal_entry is None:
        raise PurchaseValueCorrectionFifoIssuedDestinationJournalError(
            "Original historical ISSUE JournalEntry was not found"
        )

    accounting_rule_id = _positive_id(
        issue_document.accounting_rule_id,
        field="issue_document.accounting_rule_id",
    )

    rule_statement = (
        select(
            AccountingRule
        )
        .options(
            selectinload(
                AccountingRule.lines
            )
        )
        .where(
            AccountingRule.id == accounting_rule_id,
            AccountingRule.company_id == company_id,
        )
    )

    if lock:
        rule_statement = (
            rule_statement.with_for_update()
        )

    accounting_rule = (
        await db.execute(
            rule_statement
        )
    ).scalar_one_or_none()

    if accounting_rule is None:
        raise PurchaseValueCorrectionFifoIssuedDestinationRuleError(
            "Historical ISSUE accounting rule was not found"
        )

    return resolve_historical_issued_destination_from_context(
        event=event,
        issue_document=issue_document,
        issue_line=issue_line,
        journal_entry=journal_entry,
        accounting_rule=accounting_rule,
    )
