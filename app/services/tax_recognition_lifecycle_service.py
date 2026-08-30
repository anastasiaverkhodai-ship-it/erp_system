from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_calculation import TaxCalculation
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
from app.services.tax_types import TaxDirection


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
