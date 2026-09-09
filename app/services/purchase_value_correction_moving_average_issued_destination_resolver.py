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
from app.models.inventory_cost_entry import (
    InventoryCostEntry,
)
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.moving_average_movement import (
    MovingAverageMovement,
)
from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
)


ZERO = Decimal("0")
WEIGHTED_AVERAGE_MOVING = "weighted_average_moving"


class PurchaseValueCorrectionMovingAverageIssuedDestinationError(
    Exception
):
    """Base historical MA issued-destination resolution error."""


class PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
    PurchaseValueCorrectionMovingAverageIssuedDestinationError
):
    """MA replay ISSUE provenance is invalid."""


class PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
    PurchaseValueCorrectionMovingAverageIssuedDestinationError
):
    """Historical ISSUE JournalEntry provenance is invalid."""


class PurchaseValueCorrectionMovingAverageIssuedDestinationRuleError(
    PurchaseValueCorrectionMovingAverageIssuedDestinationError
):
    """Historical ISSUE accounting-rule provenance is ambiguous."""


@dataclass(
    frozen=True,
    slots=True,
)
class HistoricalMovingAverageIssuedDestination:
    account_id: int
    original_journal_entry_id: int
    accounting_rule_id: int
    moving_average_movement_id: int
    inventory_cost_entry_id: int


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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                f"{field} must be a positive integer"
            )
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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                f"{field} must be finite"
            )
        )

    return result


def _valuation_method_value(
    value,
) -> str:
    raw = getattr(
        value,
        "value",
        value,
    )
    return str(
        raw
    )


def resolve_historical_moving_average_issued_destination_from_context(
    *,
    event: PurchaseValueCorrectionMovingAverageReplayEvent,
    movement: MovingAverageMovement,
    inventory_cost_entry: InventoryCostEntry,
    issue_document_line: DocumentLine,
    issue_document: Document,
    journal_entry: JournalEntry,
    accounting_rule: AccountingRule,
) -> HistoricalMovingAverageIssuedDestination:
    """
    Resolve the exact historical debit destination used by the original
    normal moving-average ISSUE.

    Provenance must agree across:
        PVC MA replay event
        -> MovingAverageMovement
        -> InventoryCostEntry
        -> POSTED ISSUE Document
        -> original posted JournalEntry
        -> historical AccountingRule.

    The destination is never inferred from the current GOODS_COGS role.
    """

    if event.effect_kind != "issued":
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical MA issued destination requires "
                "effect_kind=issued"
            )
        )

    company_id = _positive_id(
        event.company_id,
        field="event.company_id",
    )
    movement_id = _positive_id(
        event.source_moving_average_movement_id,
        field="event.source_moving_average_movement_id",
    )
    cost_entry_id = _positive_id(
        event.source_inventory_cost_entry_id,
        field="event.source_inventory_cost_entry_id",
    )

    if movement.id != movement_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "MA replay movement identity mismatch"
            )
        )

    if movement.company_id != company_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "MA replay movement company mismatch"
            )
        )

    movement_type = _valuation_method_value(
        movement.movement_type
    ).lower()

    if movement_type != "issue":
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "MA replay source movement is not ISSUE"
            )
        )

    if movement.product_id != event.product_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "MA replay movement product mismatch"
            )
        )

    if movement.warehouse_id != event.warehouse_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "MA replay movement warehouse mismatch"
            )
        )

    if event.recognition_date < movement.movement_date:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "MA replay recognition date predates ISSUE movement"
            )
        )

    document_id = _positive_id(
        movement.document_id,
        field="movement.document_id",
    )
    document_line_id = _positive_id(
        movement.document_line_id,
        field="movement.document_line_id",
    )

    if inventory_cost_entry.id != cost_entry_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "InventoryCostEntry identity mismatch"
            )
        )

    if inventory_cost_entry.company_id != company_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "InventoryCostEntry company mismatch"
            )
        )

    if inventory_cost_entry.document_id != document_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "InventoryCostEntry document mismatch"
            )
        )

    if inventory_cost_entry.document_line_id != document_line_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "InventoryCostEntry document line mismatch"
            )
        )

    if issue_document_line.id != document_line_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical ISSUE document line identity mismatch"
            )
        )

    if issue_document_line.document_id != document_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical ISSUE document line document mismatch"
            )
        )

    if issue_document_line.product_id != event.product_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical ISSUE document line product mismatch"
            )
        )

    if issue_document_line.warehouse_id != event.warehouse_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical ISSUE document line warehouse mismatch"
            )
        )

    if (
        _valuation_method_value(
            inventory_cost_entry.valuation_method
        )
        != WEIGHTED_AVERAGE_MOVING
    ):
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "InventoryCostEntry is not weighted_average_moving"
            )
        )

    if issue_document.id != document_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical ISSUE document identity mismatch"
            )
        )

    if issue_document.company_id != company_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical ISSUE document company mismatch"
            )
        )

    if issue_document.document_type != DocumentType.ISSUE:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical MA document is not ISSUE"
            )
        )

    if issue_document.status != DocumentStatus.POSTED:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical MA ISSUE is not POSTED"
            )
        )

    if event.recognition_date < issue_document.document_date:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "MA replay recognition date predates ISSUE document"
            )
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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Historical ISSUE JournalEntry company mismatch"
            )
        )

    if journal_entry.document_id != document_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Historical JournalEntry does not belong to ISSUE document"
            )
        )

    if journal_entry.reversal_of_id is not None:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Historical ISSUE cost destination cannot use "
                "reversal JournalEntry"
            )
        )

    if journal_entry.status != JournalEntryStatus.POSTED:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Historical ISSUE JournalEntry is not POSTED"
            )
        )

    if journal_entry.accounting_rule_id != accounting_rule_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Historical ISSUE JournalEntry accounting rule mismatch"
            )
        )

    if journal_entry.entry_date > event.recognition_date:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Historical ISSUE JournalEntry postdates MA replay event"
            )
        )

    if accounting_rule.id != accounting_rule_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationRuleError(
                "Historical accounting rule identity mismatch"
            )
        )

    if accounting_rule.company_id != company_id:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationRuleError(
                "Historical accounting rule company mismatch"
            )
        )

    if accounting_rule.document_type != DocumentType.ISSUE:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationRuleError(
                "Historical accounting rule is not an ISSUE rule"
            )
        )

    rule_lines = tuple(
        accounting_rule.lines
    )

    if not rule_lines:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationRuleError(
                "Historical ISSUE accounting rule has no lines"
            )
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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationRuleError(
                "Historical ISSUE rule must expose exactly one "
                "INVENTORY_COST debit destination"
            )
        )

    if not inventory_credit_lines:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationRuleError(
                "Historical ISSUE rule has no INVENTORY_COST credit side"
            )
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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationRuleError(
                "Historical ISSUE destination and inventory credit "
                "account cannot be the same"
            )
        )

    journal_lines = tuple(
        journal_entry.lines
    )

    if not journal_lines:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Historical ISSUE JournalEntry has no lines"
            )
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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Historical ISSUE JournalEntry accounts no longer "
                "match its accounting rule"
            )
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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Historical ISSUE JournalEntry does not contain "
                "the rule-defined INVENTORY_COST debit destination"
            )
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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Historical ISSUE destination account appears "
                "on credit side"
            )
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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Historical ISSUE JournalEntry does not contain "
                "its INVENTORY_COST credit side"
            )
        )

    return HistoricalMovingAverageIssuedDestination(
        account_id=destination_account_id,
        original_journal_entry_id=journal_entry_id,
        accounting_rule_id=accounting_rule_id,
        moving_average_movement_id=movement_id,
        inventory_cost_entry_id=cost_entry_id,
    )


async def resolve_purchase_value_correction_moving_average_issued_destination(
    db: AsyncSession,
    *,
    event: PurchaseValueCorrectionMovingAverageReplayEvent,
    lock: bool = False,
) -> HistoricalMovingAverageIssuedDestination:
    if event.effect_kind != "issued":
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical MA destination resolver accepts "
                "issued impacts only"
            )
        )

    company_id = _positive_id(
        event.company_id,
        field="event.company_id",
    )
    movement_id = _positive_id(
        event.source_moving_average_movement_id,
        field="event.source_moving_average_movement_id",
    )
    cost_entry_id = _positive_id(
        event.source_inventory_cost_entry_id,
        field="event.source_inventory_cost_entry_id",
    )

    movement_statement = (
        select(
            MovingAverageMovement
        )
        .where(
            MovingAverageMovement.id == movement_id,
            MovingAverageMovement.company_id == company_id,
        )
    )

    if lock:
        movement_statement = (
            movement_statement.with_for_update()
        )

    movement = (
        await db.execute(
            movement_statement
        )
    ).scalar_one_or_none()

    if movement is None:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical moving-average ISSUE movement was not found"
            )
        )

    cost_statement = (
        select(
            InventoryCostEntry
        )
        .where(
            InventoryCostEntry.id == cost_entry_id,
            InventoryCostEntry.company_id == company_id,
        )
    )

    if lock:
        cost_statement = (
            cost_statement.with_for_update()
        )

    inventory_cost_entry = (
        await db.execute(
            cost_statement
        )
    ).scalar_one_or_none()

    if inventory_cost_entry is None:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical InventoryCostEntry was not found"
            )
        )

    document_id = _positive_id(
        movement.document_id,
        field="movement.document_id",
    )

    document_line_id = _positive_id(
        movement.document_line_id,
        field="movement.document_line_id",
    )

    document_line_statement = (
        select(
            DocumentLine
        )
        .where(
            DocumentLine.id == document_line_id,
            DocumentLine.document_id == document_id,
        )
    )

    if lock:
        document_line_statement = (
            document_line_statement.with_for_update()
        )

    issue_document_line = (
        await db.execute(
            document_line_statement
        )
    ).scalar_one_or_none()

    if issue_document_line is None:
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical ISSUE document line was not found"
            )
        )

    document_statement = (
        select(
            Document
        )
        .where(
            Document.id == document_id,
            Document.company_id == company_id,
        )
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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationSourceError(
                "Historical ISSUE document was not found"
            )
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
            JournalEntry.document_id == document_id,
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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationJournalError(
                "Original historical ISSUE JournalEntry was not found"
            )
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
        raise (
            PurchaseValueCorrectionMovingAverageIssuedDestinationRuleError(
                "Historical ISSUE accounting rule was not found"
            )
        )

    return (
        resolve_historical_moving_average_issued_destination_from_context(
            event=event,
            movement=movement,
            inventory_cost_entry=inventory_cost_entry,
            issue_document_line=issue_document_line,
            issue_document=issue_document,
            journal_entry=journal_entry,
            accounting_rule=accounting_rule,
        )
    )
