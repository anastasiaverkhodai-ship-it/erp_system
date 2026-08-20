from app.services.accounting_reversal import (
    AccountingReversalError,
    JournalEntryReversalNotFoundError,
    reverse_journal_entry,
)
from app.services.reversal_context import ReversalContext
from app.services.reversal_handler import ReversalHandlerError


class AccountingReversalHandlerError(
    ReversalHandlerError
):
    """Business error raised by the accounting reversal handler."""


class AccountingReversalHandler:
    async def reverse(
        self,
        context: ReversalContext,
    ) -> None:
        document = context.document
        journal_entry = context.original_journal_entry

        # Legacy documents created before integrated accounting
        # may legitimately have no JournalEntry.
        if journal_entry is None:
            if document.accounting_rule_id is not None:
                raise AccountingReversalHandlerError(
                    "Document requires an accounting journal "
                    "entry, but none was found"
                )

            return

        # If the document explicitly stores a rule,
        # the JournalEntry must point to the same rule.
        if (
            document.accounting_rule_id is not None
            and journal_entry.accounting_rule_id
            != document.accounting_rule_id
        ):
            raise AccountingReversalHandlerError(
                "Document accounting rule does not match "
                "the journal entry accounting rule"
            )

        try:
            await reverse_journal_entry(
                db=context.db,
                company_id=context.company_id,
                journal_entry_id=journal_entry.id,
                reversal_date=context.reversal_date,
                reversed_by=context.reversed_by,
            )
        except (
            AccountingReversalError,
            JournalEntryReversalNotFoundError,
        ) as exc:
            raise AccountingReversalHandlerError(
                str(exc)
            ) from exc