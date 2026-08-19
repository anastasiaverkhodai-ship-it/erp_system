from app.services.document_accounting import (
    DocumentAccountingError,
    generate_and_post_journal_entry_from_document,
)
from app.services.posting_context import PostingContext
from app.services.posting_handler import PostingHandlerError


class AccountingPostingHandlerError(
    PostingHandlerError
):
    """Business error raised by the accounting posting handler."""


class AccountingPostingHandler:
    async def post(
        self,
        context: PostingContext,
    ) -> None:
        try:
            # Persist warehouse/costing changes and the POSTED
            # document state inside the current transaction.
            # This is FLUSH only, not COMMIT.
            await context.db.flush()

            journal_entry = (
                await generate_and_post_journal_entry_from_document(
                    db=context.db,
                    company_id=context.company_id,
                    document_id=context.document_id,
                    accounting_rule_id=context.accounting_rule_id,
                    created_by=context.created_by,
                )
            )
        except DocumentAccountingError as exc:
            raise AccountingPostingHandlerError(
                str(exc)
            ) from exc

        context.set_journal_entry(
            journal_entry
        )