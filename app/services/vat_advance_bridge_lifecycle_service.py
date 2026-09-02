from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.vat_advance_bridge_calculation_service import (
    VatAdvanceBridgeDataIntegrityError,
)
from app.services.vat_advance_bridge_journal_service import (
    VatAdvanceBridgeJournalError,
    generate_and_post_vat_advance_bridge_journal_entry,
    reverse_vat_advance_bridge_journal_entry,
)
from app.services.vat_advance_bridge_reconciliation_service import (
    VatAdvanceBridgeReconciliationError,
    VatAdvanceBridgeReconciliationResult,
    reconcile_vat_advance_bridge_for_tax_calculation,
)


class VatAdvanceBridgeLifecycleError(Exception):
    """VAT advance bridge failed during a business lifecycle mutation."""


def _validate_context(
    *,
    company_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    created_by: int,
) -> None:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if tax_calculation_id <= 0:
        raise ValueError(
            "tax_calculation_id must be greater than zero"
        )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise ValueError(
            "adjustment_date must be a date"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )


async def _post_created_vat_advance_bridge_events(
    db: AsyncSession,
    *,
    result: VatAdvanceBridgeReconciliationResult,
    created_by: int,
) -> None:
    """
    Consume immutable bridge events in exact persistence order.

    Original event:
        Dr 702 / Cr 643

    Reversal event:
        Dr 643 / Cr 702
    """
    for event in result.created_events:
        if event.reversal_of_id is None:
            await generate_and_post_vat_advance_bridge_journal_entry(
                db,
                event=event,
                created_by=created_by,
            )
        else:
            await reverse_vat_advance_bridge_journal_entry(
                db,
                reversal_event=event,
                reversed_by=created_by,
            )


async def reconcile_vat_advance_bridge_lifecycle_for_tax_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    created_by: int,
) -> VatAdvanceBridgeReconciliationResult:
    """
    Reconcile the financial-accounting VAT advance bridge and
    immediately post/reverse the corresponding GL entries.

    Must run only after OUTPUT VAT recognition for the same
    TaxCalculation has reached its current immutable state.

    Caller owns COMMIT / ROLLBACK.
    """
    _validate_context(
        company_id=company_id,
        tax_calculation_id=tax_calculation_id,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    try:
        result = (
            await reconcile_vat_advance_bridge_for_tax_calculation(
                db,
                company_id=company_id,
                tax_calculation_id=tax_calculation_id,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )
    except (
        VatAdvanceBridgeDataIntegrityError,
        VatAdvanceBridgeReconciliationError,
    ) as exc:
        raise VatAdvanceBridgeLifecycleError(
            "VAT advance bridge reconciliation failed: "
            f"{exc}"
        ) from exc

    try:
        await _post_created_vat_advance_bridge_events(
            db,
            result=result,
            created_by=created_by,
        )
    except VatAdvanceBridgeJournalError as exc:
        raise VatAdvanceBridgeLifecycleError(
            "VAT advance bridge journal posting failed: "
            f"{exc}"
        ) from exc

    return result
