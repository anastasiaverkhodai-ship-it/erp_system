from app.services.posting_context import (
    PostingContext,
)
from app.services.posting_handler import (
    PostingHandlerError,
)
from app.services.purchase_value_correction_moving_average_lifecycle_service import (
    PurchaseValueCorrectionMovingAverageLifecycleError,
    post_created_purchase_value_correction_moving_average_peer_journals,
)


class PurchaseValueCorrectionMovingAveragePostAccountingHandlerError(
    PostingHandlerError
):
    """
    Normal ISSUE accounting must exist before typed PVC MA GL
    can resolve the historical issued destination.
    """


class PurchaseValueCorrectionMovingAveragePostAccountingHandler:

    async def post(
        self,
        context: PostingContext,
    ) -> None:
        results = (
            context
            .get_pvc_ma_issue_reconciliations()
        )

        if not results:
            return

        if (
            context.get_journal_entry()
            is None
        ):
            raise (
                PurchaseValueCorrectionMovingAveragePostAccountingHandlerError(
                    "Normal document JournalEntry must exist "
                    "before PVC moving-average GL posting"
                )
            )

        try:
            for result in results:
                await post_created_purchase_value_correction_moving_average_peer_journals(
                    context.db,
                    result=result,
                    created_by=context.created_by,
                )
        except (
            PurchaseValueCorrectionMovingAverageLifecycleError
        ) as exc:
            raise (
                PurchaseValueCorrectionMovingAveragePostAccountingHandlerError(
                    str(exc)
                )
            ) from exc
