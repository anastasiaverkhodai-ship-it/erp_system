from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_calculation import TaxCalculation
from app.models.trade_document import (
    TradeDocument,
)
from app.services.input_tax_recognition_calculation_service import (
    InputTaxRecognitionCalculationError,
)
from app.services.input_tax_recognition_candidate_loader_service import (
    InputTaxRecognitionCandidateLoaderError,
)
from app.services.input_tax_recognition_evidence_allocation_service import (
    InputTaxRecognitionEvidenceAllocationError,
)
from app.services.input_tax_recognition_persistence_service import (
    InputTaxRecognitionPersistenceError,
)
from app.services.input_tax_recognition_reconciliation_service import (
    InputTaxRecognitionReconciliationError,
    InputTaxRecognitionReconciliationResult,
    reconcile_input_tax_calculation_from_active_sources,
)

from app.services.tax_recognition_orchestration_service import (
    TaxRecognitionOrchestrationError,
)
from app.services.tax_recognition_persistence_service import (
    TaxRecognitionPersistenceError,
)
from app.services.tax_recognition_reconciliation_service import (
    OutputTaxRecognitionReconciliationResult,
    reconcile_output_tax_calculation_from_active_sources,
)
from app.services.tax_recognition_journal_service import (
    TaxRecognitionJournalError,
    generate_and_post_input_vat_recognition_journal_entry,
    generate_and_post_output_vat_recognition_journal_entry,
    reverse_input_vat_recognition_journal_entry,
    reverse_output_vat_recognition_journal_entry,
)
from app.services.vat_advance_bridge_lifecycle_service import (
    VatAdvanceBridgeLifecycleError,
    reconcile_vat_advance_bridge_lifecycle_for_tax_calculation,
)
from app.services.tax_types import TaxDirection
from app.services.trade_document_types import (
    TradeDirection,
)


class TaxRecognitionLifecycleError(Exception):
    """VAT recognition failed during a business lifecycle mutation."""


def _validate_context(
    *,
    company_id: int,
    invoice_id: int,
    adjustment_date: date,
    created_by: int,
) -> None:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if invoice_id <= 0:
        raise ValueError(
            "invoice_id must be greater than zero"
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


async def _get_output_tax_calculation_ids(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int | None,
) -> tuple[int, ...]:
    statement = (
        select(
            TaxCalculation.id
        )
        .where(
            TaxCalculation.company_id
            == company_id,
            TaxCalculation.trade_document_id
            == invoice_id,
            TaxCalculation.direction
            == TaxDirection.OUTPUT,
        )
        .order_by(
            TaxCalculation.id
        )
    )

    if invoice_line_id is not None:
        if invoice_line_id <= 0:
            raise ValueError(
                "invoice_line_id must be "
                "greater than zero"
            )

        statement = statement.where(
            TaxCalculation.trade_document_line_id
            == invoice_line_id
        )

    result = await db.execute(
        statement
    )

    return tuple(
        int(calculation_id)
        for calculation_id
        in result.scalars().all()
    )


async def _post_created_output_vat_recognition_events(
    db: AsyncSession,
    *,
    result: OutputTaxRecognitionReconciliationResult,
    created_by: int,
) -> None:
    """
    Post GL effects for newly persisted immutable OUTPUT VAT events.

    created_events is consumed exactly in reconciliation /
    persistence order.

    Original fulfillment event:
        Dr GOODS_REVENUE / Cr TAX_SETTLEMENT

    Original settlement event:
        Dr VAT_OUTPUT / Cr TAX_SETTLEMENT

    Reversal event:
        reverse the original VAT JournalEntry and bind the
        reversal JournalEntry to this reversal TaxRecognitionEvent.

    Zero-tax events are intentionally GL no-ops.
    """
    for event in result.created_events:
        if event.reversal_of_id is None:
            await generate_and_post_output_vat_recognition_journal_entry(
                db,
                event=event,
                created_by=created_by,
            )
            continue

        await reverse_output_vat_recognition_journal_entry(
            db,
            reversal_event=event,
            reversed_by=created_by,
        )


async def _reconcile_ids(
    db: AsyncSession,
    *,
    company_id: int,
    calculation_ids: tuple[int, ...],
    adjustment_date: date,
    created_by: int,
) -> tuple[
    OutputTaxRecognitionReconciliationResult,
    ...,
]:
    results = []

    for calculation_id in calculation_ids:
        try:
            result = (
                await reconcile_output_tax_calculation_from_active_sources(
                    db,
                    company_id=company_id,
                    tax_calculation_id=(
                        calculation_id
                    ),
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=created_by,
                )
            )

        except (
            TaxRecognitionPersistenceError,
            TaxRecognitionOrchestrationError,
        ) as exc:
            raise TaxRecognitionLifecycleError(
                "OUTPUT VAT recognition "
                "reconciliation failed: "
                f"{exc}"
            ) from exc

        try:
            await _post_created_output_vat_recognition_events(
                db,
                result=result,
                created_by=created_by,
            )
        except TaxRecognitionJournalError as exc:
            raise TaxRecognitionLifecycleError(
                "OUTPUT VAT recognition "
                "journal posting failed: "
                f"{exc}"
            ) from exc

        try:
            await (
                reconcile_vat_advance_bridge_lifecycle_for_tax_calculation(
                    db,
                    company_id=company_id,
                    tax_calculation_id=(
                        calculation_id
                    ),
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=created_by,
                )
            )
        except VatAdvanceBridgeLifecycleError as exc:
            raise TaxRecognitionLifecycleError(
                "VAT advance bridge lifecycle failed: "
                f"{exc}"
            ) from exc

        results.append(
            result
        )

    return tuple(
        results
    )


async def reconcile_output_tax_for_invoice_line(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> tuple[
    OutputTaxRecognitionReconciliationResult,
    ...,
]:
    """
    Reconcile OUTPUT VAT affected by one invoice line.

    Purchase INPUT VAT and non-VAT invoice lines naturally
    produce no OUTPUT TaxCalculation IDs and therefore no
    automatic recognition side effect.
    """

    _validate_context(
        company_id=company_id,
        invoice_id=invoice_id,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    if invoice_line_id <= 0:
        raise ValueError(
            "invoice_line_id must be "
            "greater than zero"
        )

    calculation_ids = (
        await _get_output_tax_calculation_ids(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
            invoice_line_id=(
                invoice_line_id
            ),
        )
    )

    return await _reconcile_ids(
        db,
        company_id=company_id,
        calculation_ids=calculation_ids,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )


async def reconcile_output_tax_for_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    adjustment_date: date,
    created_by: int,
) -> tuple[
    OutputTaxRecognitionReconciliationResult,
    ...,
]:
    """
    Reconcile every OUTPUT VAT calculation for one invoice.

    Settlement allocation is invoice-level, therefore every
    VAT line must be recalculated against the new payment
    evidence.
    """

    _validate_context(
        company_id=company_id,
        invoice_id=invoice_id,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    calculation_ids = (
        await _get_output_tax_calculation_ids(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
            invoice_line_id=None,
        )
    )

    return await _reconcile_ids(
        db,
        company_id=company_id,
        calculation_ids=calculation_ids,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

async def _get_input_tax_calculation_ids(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int | None,
) -> tuple[int, ...]:
    """
    Return persistent INPUT VAT TaxCalculation IDs affected by
    one Purchase Invoice or one Purchase Invoice line.

    Non-VAT and Sales Invoice sources naturally return no rows.
    """

    statement = (
        select(
            TaxCalculation.id
        )
        .where(
            TaxCalculation.company_id
            == company_id,
            TaxCalculation.trade_document_id
            == invoice_id,
            TaxCalculation.direction
            == TaxDirection.INPUT,
        )
        .order_by(
            TaxCalculation.id
        )
    )

    if invoice_line_id is not None:
        if invoice_line_id <= 0:
            raise ValueError(
                "invoice_line_id must be "
                "greater than zero"
            )

        statement = statement.where(
            TaxCalculation.trade_document_line_id
            == invoice_line_id
        )

    result = await db.execute(
        statement
    )

    return tuple(
        int(calculation_id)
        for calculation_id
        in result.scalars().all()
    )


async def _post_created_input_vat_recognition_events(
    db: AsyncSession,
    *,
    result: InputTaxRecognitionReconciliationResult,
    created_by: int,
) -> None:
    """
    Post GL effects for newly persisted immutable INPUT VAT events.

    Original evidence-backed recognition:
        Dr TAX_SETTLEMENT / Cr VAT_INPUT

    Reversal:
        reverse the original JournalEntry and bind the reversal
        JournalEntry to the immutable reversal TaxRecognitionEvent.

    Zero-tax events are GL no-ops.
    """
    for event in result.created_events:
        if event.reversal_of_id is None:
            await generate_and_post_input_vat_recognition_journal_entry(
                db,
                event=event,
                created_by=created_by,
            )
            continue

        await reverse_input_vat_recognition_journal_entry(
            db,
            reversal_event=event,
            reversed_by=created_by,
        )


async def post_created_input_vat_recognition_events(
    db: AsyncSession,
    *,
    result: InputTaxRecognitionReconciliationResult,
    created_by: int,
) -> None:
    """
    Public transaction-owned INPUT VAT GL orchestration for one
    completed evidence-gated reconciliation result.

    This is intentionally reusable by both:
        - ordinary purchase economic-source lifecycle; and
        - TaxCreditEvidence mutation lifecycle.
    """
    await _post_created_input_vat_recognition_events(
        db,
        result=result,
        created_by=created_by,
    )


async def _reconcile_input_ids(
    db: AsyncSession,
    *,
    company_id: int,
    calculation_ids: tuple[int, ...],
    adjustment_date: date,
    created_by: int,
) -> tuple[
    InputTaxRecognitionReconciliationResult,
    ...,
]:
    """
    Reconcile automatic evidence-gated INPUT VAT calculations.

    Newly persisted immutable TaxRecognitionEvent rows are posted
    to GL in reconciliation / persistence order.
    """

    results = []

    for calculation_id in calculation_ids:
        try:
            result = (
                await reconcile_input_tax_calculation_from_active_sources(
                    db,
                    company_id=company_id,
                    tax_calculation_id=(
                        calculation_id
                    ),
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=created_by,
                )
            )

        except (
            InputTaxRecognitionCalculationError,
            InputTaxRecognitionCandidateLoaderError,
            InputTaxRecognitionEvidenceAllocationError,
            InputTaxRecognitionPersistenceError,
            InputTaxRecognitionReconciliationError,
        ) as exc:
            raise TaxRecognitionLifecycleError(
                "INPUT VAT recognition "
                "reconciliation failed: "
                f"{exc}"
            ) from exc

        try:
            await _post_created_input_vat_recognition_events(
                db,
                result=result,
                created_by=created_by,
            )
        except TaxRecognitionJournalError as exc:
            raise TaxRecognitionLifecycleError(
                "INPUT VAT recognition "
                "journal posting failed: "
                f"{exc}"
            ) from exc

        results.append(
            result
        )

    return tuple(
        results
    )


async def reconcile_input_tax_for_invoice_line(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> tuple[
    InputTaxRecognitionReconciliationResult,
    ...,
]:
    """
    Reconcile INPUT VAT affected by one Purchase Invoice line.

    Sales OUTPUT VAT and non-VAT lines naturally produce no INPUT
    TaxCalculation IDs and therefore no INPUT recognition effect.
    """

    _validate_context(
        company_id=company_id,
        invoice_id=invoice_id,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    if invoice_line_id <= 0:
        raise ValueError(
            "invoice_line_id must be "
            "greater than zero"
        )

    calculation_ids = (
        await _get_input_tax_calculation_ids(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
            invoice_line_id=(
                invoice_line_id
            ),
        )
    )

    return await _reconcile_input_ids(
        db,
        company_id=company_id,
        calculation_ids=calculation_ids,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )


async def reconcile_input_tax_for_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    adjustment_date: date,
    created_by: int,
) -> tuple[
    InputTaxRecognitionReconciliationResult,
    ...,
]:
    """
    Reconcile every INPUT VAT calculation for one Purchase Invoice.

    Settlement allocation is invoice-level, therefore all INPUT VAT
    lines are recalculated against current receipt/payment economics
    and current TaxCreditEvidence capacity.
    """

    _validate_context(
        company_id=company_id,
        invoice_id=invoice_id,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    calculation_ids = (
        await _get_input_tax_calculation_ids(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
            invoice_line_id=None,
        )
    )

    return await _reconcile_input_ids(
        db,
        company_id=company_id,
        calculation_ids=calculation_ids,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

async def _get_tax_invoice_direction(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
) -> TradeDirection:
    """
    Load immutable business direction used to dispatch VAT
    recognition lifecycle:

        SALE     -> OUTPUT VAT
        PURCHASE -> INPUT VAT
    """

    direction = (
        await db.execute(
            select(
                TradeDocument.direction
            ).where(
                TradeDocument.company_id
                == company_id,
                TradeDocument.id
                == invoice_id,
            )
        )
    ).scalar_one_or_none()

    if direction is None:
        raise TaxRecognitionLifecycleError(
            "Trade Invoice not found during "
            "VAT recognition lifecycle"
        )

    try:
        return TradeDirection(
            direction
        )

    except ValueError as exc:
        raise TaxRecognitionLifecycleError(
            "Trade Invoice has unsupported "
            "direction during VAT recognition lifecycle"
        ) from exc


async def reconcile_tax_for_invoice_line(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> tuple[
    OutputTaxRecognitionReconciliationResult
    | InputTaxRecognitionReconciliationResult,
    ...,
]:
    """
    Direction-aware VAT lifecycle entry point for one invoice line.

    SALE:
        reconcile OUTPUT VAT.

    PURCHASE:
        reconcile evidence-gated INPUT VAT.
    """

    _validate_context(
        company_id=company_id,
        invoice_id=invoice_id,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    if invoice_line_id <= 0:
        raise ValueError(
            "invoice_line_id must be "
            "greater than zero"
        )

    direction = (
        await _get_tax_invoice_direction(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
        )
    )

    if direction == TradeDirection.SALE:
        return (
            await reconcile_output_tax_for_invoice_line(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                invoice_line_id=invoice_line_id,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )

    if direction == TradeDirection.PURCHASE:
        return (
            await reconcile_input_tax_for_invoice_line(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                invoice_line_id=invoice_line_id,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )

    raise TaxRecognitionLifecycleError(
        "Unsupported Trade Invoice direction "
        "during VAT recognition lifecycle"
    )


async def reconcile_tax_for_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    adjustment_date: date,
    created_by: int,
) -> tuple[
    OutputTaxRecognitionReconciliationResult
    | InputTaxRecognitionReconciliationResult,
    ...,
]:
    """
    Direction-aware VAT lifecycle entry point for one invoice.

    Used by PaymentSettlementAllocation lifecycle because settlement
    changes economic capacity for the whole invoice.
    """

    _validate_context(
        company_id=company_id,
        invoice_id=invoice_id,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    direction = (
        await _get_tax_invoice_direction(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
        )
    )

    if direction == TradeDirection.SALE:
        return (
            await reconcile_output_tax_for_invoice(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )

    if direction == TradeDirection.PURCHASE:
        return (
            await reconcile_input_tax_for_invoice(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )

    raise TaxRecognitionLifecycleError(
        "Unsupported Trade Invoice direction "
        "during VAT recognition lifecycle"
    )
