from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_invoice import TaxInvoice
from app.models.tax_invoice_correction import (
    TaxInvoiceCorrection,
)
from app.models.tax_invoice_correction_line import (
    TaxInvoiceCorrectionLine,
)
from app.models.tax_invoice_registration_event import (
    TaxInvoiceRegistrationEvent,
)
from app.services.tax_invoice_correction_registration_lifecycle_service import (
    validate_tax_invoice_correction_document_date,
)
from app.services.tax_invoice_correction_source_resolver_service import (
    ResolvedTaxInvoiceCorrectionLine,
    TaxInvoiceCorrectionLineSource,
    resolve_tax_invoice_correction_lines,
)


class TaxInvoiceCorrectionPersistenceError(Exception):
    """Base canonical RK persistence error."""


class TaxInvoiceCorrectionOriginalInvoiceError(
    TaxInvoiceCorrectionPersistenceError
):
    """Original canonical PN is absent or not legally usable."""


class TaxInvoiceCorrectionIdempotencyError(
    TaxInvoiceCorrectionPersistenceError
):
    """Repeated economic-source request conflicts with persisted RK."""


def _positive_int(
    value: int,
    *,
    field: str,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise TaxInvoiceCorrectionPersistenceError(
            f"{field} must be a positive integer"
        )

    return value


def _document_number(
    value: object,
) -> str:
    if value is None:
        raise TaxInvoiceCorrectionPersistenceError(
            "document_number is required"
        )

    normalized = str(
        value
    ).strip()

    if not normalized:
        raise TaxInvoiceCorrectionPersistenceError(
            "document_number is required"
        )

    if len(normalized) > 120:
        raise TaxInvoiceCorrectionPersistenceError(
            "document_number exceeds 120 characters"
        )

    return normalized


async def _load_registered_original_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    original_tax_invoice_id: int,
) -> TaxInvoice:
    invoice = await db.scalar(
        select(
            TaxInvoice
        )
        .where(
            TaxInvoice.company_id
            == company_id,
            TaxInvoice.id
            == original_tax_invoice_id,
        )
        .with_for_update()
    )

    if invoice is None:
        raise TaxInvoiceCorrectionOriginalInvoiceError(
            "original canonical PN does not belong "
            "to company or does not exist"
        )

    latest = await db.scalar(
        select(
            TaxInvoiceRegistrationEvent
        )
        .where(
            TaxInvoiceRegistrationEvent.company_id
            == company_id,
            TaxInvoiceRegistrationEvent.tax_invoice_id
            == original_tax_invoice_id,
        )
        .order_by(
            TaxInvoiceRegistrationEvent.event_date.desc(),
            TaxInvoiceRegistrationEvent.id.desc(),
        )
        .limit(1)
        .with_for_update()
    )

    if (
        latest is None
        or latest.status != "registered"
    ):
        raise TaxInvoiceCorrectionOriginalInvoiceError(
            "canonical RK requires latest original PN "
            "registration status = registered"
        )

    return invoice


def _source_filter(
    *,
    company_id: int,
    resolved: ResolvedTaxInvoiceCorrectionLine,
):
    conditions = [
        TaxInvoiceCorrectionLine.company_id
        == company_id
    ]

    if resolved.source_kind == "sales_return":
        conditions.append(
            TaxInvoiceCorrectionLine
            .sales_return_recognition_event_id
            == resolved.source_id
        )

    elif (
        resolved.source_kind
        == "sales_value_correction"
    ):
        conditions.append(
            TaxInvoiceCorrectionLine
            .trade_value_correction_event_id
            == resolved.source_id
        )

    elif resolved.source_kind == "purchase_return":
        conditions.append(
            TaxInvoiceCorrectionLine
            .purchase_return_vat_adjustment_event_id
            == resolved.source_id
        )

    elif (
        resolved.source_kind
        == "purchase_value_correction"
    ):
        conditions.append(
            TaxInvoiceCorrectionLine
            .purchase_value_correction_vat_adjustment_event_id
            == resolved.source_id
        )

    elif (
        resolved.source_kind
        == "recognition_reversal"
    ):
        conditions.append(
            TaxInvoiceCorrectionLine
            .tax_recognition_reversal_event_id
            == resolved.source_id
        )

    else:
        raise TaxInvoiceCorrectionPersistenceError(
            "unsupported RK source_kind"
        )

    return conditions


async def _load_existing_source_lines(
    db: AsyncSession,
    *,
    company_id: int,
    resolved: tuple[
        ResolvedTaxInvoiceCorrectionLine,
        ...,
    ],
) -> list[
    TaxInvoiceCorrectionLine | None
]:
    result = []

    for item in resolved:
        if item.source_kind == 'metadata_correction':
            result.append(None)
            continue
        existing = await db.scalar(
            select(
                TaxInvoiceCorrectionLine
            )
            .where(
                *_source_filter(
                    company_id=company_id,
                    resolved=item,
                )
            )
            .with_for_update()
        )

        result.append(
            existing
        )

    return result


def _same_decimal(
    left: object,
    right: Decimal,
) -> bool:
    return Decimal(
        str(left)
    ) == Decimal(
        str(right)
    )


def _line_matches(
    *,
    persisted: TaxInvoiceCorrectionLine,
    resolved: ResolvedTaxInvoiceCorrectionLine,
) -> bool:
    return all(
        (
            persisted.line_number
            == resolved.line_number,
            persisted.original_tax_invoice_line_id
            == resolved.original_tax_invoice_line_id,
            persisted.source_kind
            == resolved.source_kind,
            persisted.sales_return_recognition_event_id
            == resolved.sales_return_recognition_event_id,
            persisted.trade_value_correction_event_id
            == resolved.trade_value_correction_event_id,
            persisted.purchase_return_vat_adjustment_event_id
            == resolved.purchase_return_vat_adjustment_event_id,
            persisted.purchase_value_correction_vat_adjustment_event_id
            == resolved.purchase_value_correction_vat_adjustment_event_id,
            persisted.tax_recognition_reversal_event_id
            == resolved.tax_recognition_reversal_event_id,
            persisted.tax_credit_evidence_id
            == resolved.tax_credit_evidence_id,
            persisted.reason_code
            == resolved.reason_code,
            persisted.description
            == resolved.description,
            persisted.uom_code
            == resolved.uom_code,
            persisted.classification_kind
            == resolved.classification_kind,
            persisted.statutory_code
            == resolved.statutory_code,
            persisted.tax_rate_code
            == resolved.tax_rate_code,
            _same_decimal(
                persisted.tax_rate,
                resolved.tax_rate,
            ),
            _same_decimal(
                persisted.quantity_delta,
                resolved.quantity_delta,
            ),
            _same_decimal(
                persisted.unit_price_without_vat_delta,
                resolved.unit_price_without_vat_delta,
            ),
            _same_decimal(
                persisted.taxable_base_delta,
                resolved.taxable_base_delta,
            ),
            _same_decimal(
                persisted.tax_amount_delta,
                resolved.tax_amount_delta,
            ),
            _same_decimal(
                persisted.total_with_vat_delta,
                resolved.total_with_vat_delta,
            ),
        )
    )


async def create_tax_invoice_correction(
    db: AsyncSession,
    *,
    company_id: int,
    original_tax_invoice_id: int,
    document_number: str,
    document_date: date,
    sources: list[
        TaxInvoiceCorrectionLineSource
    ],
    created_by: int,
) -> TaxInvoiceCorrection:
    """
    Create/reuse one canonical legal RK from existing immutable
    economic VAT-correction sources.

    This service does not mutate economic events and does not create
    registration events. Caller owns commit/rollback.
    """

    company_id = _positive_int(
        company_id,
        field="company_id",
    )

    original_tax_invoice_id = _positive_int(
        original_tax_invoice_id,
        field="original_tax_invoice_id",
    )

    created_by = _positive_int(
        created_by,
        field="created_by",
    )

    number = _document_number(
        document_number
    )

    if not isinstance(
        document_date,
        date,
    ):
        raise TaxInvoiceCorrectionPersistenceError(
            "document_date must be a date"
        )

    original_invoice = (
        await _load_registered_original_invoice(
            db,
            company_id=company_id,
            original_tax_invoice_id=(
                original_tax_invoice_id
            ),
        )
    )

    validate_tax_invoice_correction_document_date(
        original_invoice_date=(
            original_invoice.document_date
        ),
        correction_document_date=(
            document_date
        ),
    )

    resolved = (
        await resolve_tax_invoice_correction_lines(
            db,
            company_id=company_id,
            original_invoice=original_invoice,
            correction_document_number=number,
            correction_document_date=document_date,
            sources=sources,
        )
    )

    existing_lines = (
        await _load_existing_source_lines(
            db,
            company_id=company_id,
            resolved=resolved,
        )
    )

    existing_count = sum(
        item is not None
        for item in existing_lines
    )

    if existing_count:
        if existing_count != len(
            resolved
        ):
            raise TaxInvoiceCorrectionIdempotencyError(
                "only part of requested RK economic-source set "
                "is already persisted"
            )

        correction_ids = {
            item.tax_invoice_correction_id
            for item in existing_lines
            if item is not None
        }

        if len(correction_ids) != 1:
            raise TaxInvoiceCorrectionIdempotencyError(
                "requested RK sources belong to multiple "
                "canonical correction documents"
            )

        correction_id = next(
            iter(
                correction_ids
            )
        )

        existing_header = await db.scalar(
            select(
                TaxInvoiceCorrection
            )
            .where(
                TaxInvoiceCorrection.company_id
                == company_id,
                TaxInvoiceCorrection.id
                == correction_id,
            )
            .with_for_update()
        )

        if existing_header is None:
            raise TaxInvoiceCorrectionIdempotencyError(
                "persisted RK source line points to missing header"
            )

        if not all(
            (
                existing_header.original_tax_invoice_id
                == original_tax_invoice_id,
                existing_header.direction
                == original_invoice.direction,
                existing_header.document_number
                == number,
                existing_header.document_date
                == document_date,
                existing_header.currency_code
                == "UAH",
            )
        ):
            raise TaxInvoiceCorrectionIdempotencyError(
                "existing RK header conflicts with repeated request"
            )

        persisted_by_source = {
            (
                item.source_kind,
                (
                    item.sales_return_recognition_event_id
                    or item.trade_value_correction_event_id
                    or item.purchase_return_vat_adjustment_event_id
                    or item.purchase_value_correction_vat_adjustment_event_id
                    or item.tax_recognition_reversal_event_id
                ),
            ): item
            for item in existing_lines
            if item is not None
        }

        for item in resolved:
            persisted = persisted_by_source.get(
                (
                    item.source_kind,
                    item.source_id,
                )
            )

            if (
                persisted is None
                or not _line_matches(
                    persisted=persisted,
                    resolved=item,
                )
            ):
                raise TaxInvoiceCorrectionIdempotencyError(
                    "existing RK line snapshot conflicts "
                    "with repeated request"
                )

        return existing_header

    correction = TaxInvoiceCorrection(
        company_id=company_id,
        original_tax_invoice_id=(
            original_invoice.id
        ),
        direction=(
            original_invoice.direction
        ),
        document_number=number,
        document_date=document_date,
        currency_code=(
            original_invoice.currency_code
        ),
        seller_name=(
            original_invoice.seller_name
        ),
        seller_tax_number=(
            original_invoice.seller_tax_number
        ),
        seller_vat_number=(
            original_invoice.seller_vat_number
        ),
        buyer_name=(
            original_invoice.buyer_name
        ),
        buyer_tax_number=(
            original_invoice.buyer_tax_number
        ),
        buyer_vat_number=(
            original_invoice.buyer_vat_number
        ),
        created_by=created_by,
        registration_party=('buyer' if original_invoice.buyer_vat_number and sum((r.total_with_vat_delta for r in resolved),Decimal('0')) < 0 else 'seller'),
    )

    db.add(
        correction
    )

    await db.flush()

    for item in resolved:
        db.add(
            TaxInvoiceCorrectionLine(
                company_id=company_id,
                tax_invoice_correction_id=(
                    correction.id
                ),
                line_number=(
                    item.line_number
                ),
                original_tax_invoice_line_id=(
                    item.original_tax_invoice_line_id
                ),
                source_kind=(
                    item.source_kind
                ),
                sales_return_recognition_event_id=(
                    item.sales_return_recognition_event_id
                ),
                trade_value_correction_event_id=(
                    item.trade_value_correction_event_id
                ),
                purchase_return_vat_adjustment_event_id=(
                    item.purchase_return_vat_adjustment_event_id
                ),
                purchase_value_correction_vat_adjustment_event_id=(
                    item.purchase_value_correction_vat_adjustment_event_id
                ),
                tax_recognition_reversal_event_id=(
                    item.tax_recognition_reversal_event_id
                ),
                tax_credit_evidence_id=(
                    item.tax_credit_evidence_id
                ),
                reason_code=(
                    item.reason_code
                ),
                description=(
                    item.description
                ),
                uom_code=(
                    item.uom_code
                ),
                classification_kind=(
                    item.classification_kind
                ),
                statutory_code=(
                    item.statutory_code
                ),
                tax_rate_code=(
                    item.tax_rate_code
                ),
                tax_rate=(
                    item.tax_rate
                ),
                quantity_delta=(
                    item.quantity_delta
                ),
                unit_price_without_vat_delta=(
                    item.unit_price_without_vat_delta
                ),
                taxable_base_delta=(
                    item.taxable_base_delta
                ),
                tax_amount_delta=(
                    item.tax_amount_delta
                ),
                total_with_vat_delta=(
                    item.total_with_vat_delta
                ),
            )
        )

    await db.flush()

    return correction
