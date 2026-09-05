from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.purchase_return_input_vat_credit_correction_journal_service import (
    PurchaseReturnInputVatCreditCorrectionJournalError,
    generate_and_post_purchase_return_input_vat_credit_correction_journal_entry,
    reverse_purchase_return_input_vat_credit_correction_journal_entry,
)
from app.services.purchase_return_input_vat_credit_correction_reconciliation_service import (
    PurchaseReturnInputVatCreditCorrectionReconciliationError,
    PurchaseReturnInputVatCreditCorrectionReconciliationResult,
    reconcile_purchase_return_input_vat_credit_corrections_for_tax_calculation,
)


class PurchaseReturnInputVatCreditCorrectionLifecycleError(
    Exception
):
    """Legal Purchase Return INPUT VAT correction lifecycle failed."""


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value <= 0
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionLifecycleError(
                f"{field} must be greater than zero"
            )
        )

    return value


def _business_date(
    value: date,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionLifecycleError(
                "adjustment_date must be a date"
            )
        )

    return value


async def _post_created_legal_correction_events(
    db: AsyncSession,
    *,
    result: PurchaseReturnInputVatCreditCorrectionReconciliationResult,
    created_by: int,
) -> None:
    """
    Consume immutable reconciliation output in exact persistence order.

    Originals create Dr644 / Cr641.

    Reversals reverse the historical legal correction JournalEntry,
    producing Dr641 / Cr644.

    Zero-tax events are intentionally dispatched to the journal layer;
    that layer owns the explicit no-zero-JE rule.
    """

    for event in result.created_events:
        try:
            if event.reversal_of_id is None:
                await (
                    generate_and_post_purchase_return_input_vat_credit_correction_journal_entry(
                        db,
                        event=event,
                        created_by=created_by,
                    )
                )
            else:
                await (
                    reverse_purchase_return_input_vat_credit_correction_journal_entry(
                        db,
                        reversal_event=event,
                        reversed_by=created_by,
                    )
                )

        except (
            PurchaseReturnInputVatCreditCorrectionJournalError
        ) as exc:
            raise (
                PurchaseReturnInputVatCreditCorrectionLifecycleError(
                    "Legal INPUT VAT credit correction "
                    f"journal dispatch failed for event {event.id}: "
                    f"{exc}"
                )
            ) from exc


async def reconcile_purchase_return_input_vat_credit_correction_lifecycle_for_tax_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    created_by: int,
) -> PurchaseReturnInputVatCreditCorrectionReconciliationResult:
    """
    Reconcile legal buyer-side Purchase Return INPUT VAT correction
    and post the immutable GL consequences in exact persistence order.

    Transaction ownership remains with the caller.

    This lifecycle does not mutate TaxRecognitionEvent,
    TaxCreditEvidence, TaxCalculation, economic VAT bridge facts,
    PurchaseReturnVatAdjustmentEvent, or supplier advances.
    """

    company = _positive_id(
        company_id,
        field="company_id",
    )

    calculation = _positive_id(
        tax_calculation_id,
        field="tax_calculation_id",
    )

    user = _positive_id(
        created_by,
        field="created_by",
    )

    business_date = _business_date(
        adjustment_date
    )

    try:
        result = (
            await reconcile_purchase_return_input_vat_credit_corrections_for_tax_calculation(
                db,
                company_id=company,
                tax_calculation_id=calculation,
                adjustment_date=business_date,
                created_by=user,
            )
        )

    except (
        PurchaseReturnInputVatCreditCorrectionReconciliationError
    ) as exc:
        raise (
            PurchaseReturnInputVatCreditCorrectionLifecycleError(
                "Legal INPUT VAT credit correction "
                f"reconciliation failed: {exc}"
            )
        ) from exc

    await _post_created_legal_correction_events(
        db,
        result=result,
        created_by=user,
    )

    return result
