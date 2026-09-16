from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.counterparty import Counterparty
from app.models.input_vat_credit_claim import InputVatCreditClaim
from app.models.tax_calculation import TaxCalculation
from app.models.tax_credit_evidence import TaxCreditEvidence
from app.models.tax_invoice import TaxInvoice
from app.models.tax_invoice_credit_evidence_link import (
    TaxInvoiceCreditEvidenceLink,
)
from app.models.tax_invoice_line import TaxInvoiceLine
from app.models.trade_document import TradeDocument
from app.services.tax_invoice_registration_lifecycle_service import (
    append_tax_invoice_registration_event,
)


class InputTaxInvoiceError(Exception):
    """Base canonical INPUT tax-invoice error."""


class InputTaxInvoiceEvidenceError(InputTaxInvoiceError):
    """10.5 claim/evidence provenance is inconsistent."""


class InputTaxInvoiceSnapshotError(InputTaxInvoiceError):
    """Legal line snapshot is invalid or incomplete."""


class InputTaxInvoiceIdempotencyError(InputTaxInvoiceError):
    """Existing evidence links conflict with this request."""


@dataclass(frozen=True)
class InputTaxInvoiceLineAttestation:
    line_number: int
    claim_id: int
    description: str
    quantity: Decimal
    uom_code: str
    classification_kind: str
    statutory_code: str


def _enum_text(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().lower()


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise InputTaxInvoiceSnapshotError(
            f"{field} must be a positive integer"
        )

    return value


def _required_text(
    value: object,
    *,
    field: str,
    max_length: int | None = None,
) -> str:
    if value is None:
        raise InputTaxInvoiceSnapshotError(
            f"{field} is required"
        )

    normalized = str(value).strip()

    if not normalized:
        raise InputTaxInvoiceSnapshotError(
            f"{field} is required"
        )

    if (
        max_length is not None
        and len(normalized) > max_length
    ):
        raise InputTaxInvoiceSnapshotError(
            f"{field} exceeds maximum length"
        )

    return normalized


def _parse_date(
    value: object,
    *,
    field: str,
) -> date:
    if isinstance(value, date):
        return value

    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise InputTaxInvoiceEvidenceError(
                f"{field} must be an ISO date"
            ) from exc

    raise InputTaxInvoiceEvidenceError(
        f"{field} is required"
    )


def _claim_attestation(
    claim: InputVatCreditClaim,
) -> dict:
    payload = claim.attestation

    if not isinstance(payload, dict):
        raise InputTaxInvoiceEvidenceError(
            "INPUT VAT claim attestation must be an object"
        )

    if payload.get("registration_status") != "registered":
        raise InputTaxInvoiceEvidenceError(
            "10.6 INPUT V1 requires registered PN evidence"
        )

    required = (
        "invoice_number",
        "invoice_date",
        "registered_on",
        "receipt_reference",
        "buyer_vat_number",
        "supplier_vat_number",
    )

    missing = [
        key
        for key in required
        if payload.get(key) in (None, "")
    ]

    if missing:
        raise InputTaxInvoiceEvidenceError(
            f"claim attestation fields missing: {missing}"
        )

    return payload


def validate_input_line_attestations(
    lines: list[InputTaxInvoiceLineAttestation],
) -> tuple[InputTaxInvoiceLineAttestation, ...]:
    if not lines:
        raise InputTaxInvoiceSnapshotError(
            "at least one INPUT tax-invoice line is required"
        )

    normalized = []

    for line in lines:
        line_number = _positive_id(
            line.line_number,
            field="line_number",
        )

        claim_id = _positive_id(
            line.claim_id,
            field="claim_id",
        )

        quantity = Decimal(line.quantity)

        if (
            not quantity.is_finite()
            or quantity <= 0
        ):
            raise InputTaxInvoiceSnapshotError(
                "quantity must be finite and positive"
            )

        kind = _required_text(
            line.classification_kind,
            field="classification_kind",
            max_length=10,
        ).lower()

        if kind not in {
            "uktzed",
            "dkpp",
        }:
            raise InputTaxInvoiceSnapshotError(
                "classification_kind must be uktzed or dkpp"
            )

        normalized.append(
            InputTaxInvoiceLineAttestation(
                line_number=line_number,
                claim_id=claim_id,
                description=_required_text(
                    line.description,
                    field="description",
                    max_length=500,
                ),
                quantity=quantity,
                uom_code=_required_text(
                    line.uom_code,
                    field="uom_code",
                    max_length=20,
                ).lower(),
                classification_kind=kind,
                statutory_code=_required_text(
                    line.statutory_code,
                    field="statutory_code",
                    max_length=32,
                ),
            )
        )

    line_numbers = {
        line.line_number
        for line in normalized
    }

    claim_ids = {
        line.claim_id
        for line in normalized
    }

    if len(line_numbers) != len(normalized):
        raise InputTaxInvoiceSnapshotError(
            "line_number values must be unique"
        )

    if len(claim_ids) != len(normalized):
        raise InputTaxInvoiceSnapshotError(
            "claim_id values must be unique"
        )

    return tuple(
        sorted(
            normalized,
            key=lambda line: line.line_number,
        )
    )


async def _load_claims(
    db: AsyncSession,
    *,
    company_id: int,
    claim_ids: tuple[int, ...],
) -> dict[int, InputVatCreditClaim]:
    rows = list(
        (
            await db.scalars(
                select(InputVatCreditClaim)
                .where(
                    InputVatCreditClaim.company_id
                    == company_id,
                    InputVatCreditClaim.id.in_(
                        claim_ids
                    ),
                )
                .order_by(InputVatCreditClaim.id)
                .with_for_update()
            )
        ).all()
    )

    result = {
        row.id: row
        for row in rows
    }

    if set(result) != set(claim_ids):
        raise InputTaxInvoiceEvidenceError(
            "one or more INPUT VAT claims do not belong "
            "to the company or do not exist"
        )

    return result


async def _existing_invoice_for_evidence(
    db: AsyncSession,
    *,
    company_id: int,
    evidence_ids: tuple[int, ...],
) -> TaxInvoice | None:
    links = list(
        (
            await db.scalars(
                select(TaxInvoiceCreditEvidenceLink)
                .where(
                    TaxInvoiceCreditEvidenceLink.company_id
                    == company_id,
                    TaxInvoiceCreditEvidenceLink
                    .tax_credit_evidence_id
                    .in_(evidence_ids),
                )
                .with_for_update()
            )
        ).all()
    )

    if not links:
        return None

    if len(links) != len(evidence_ids):
        raise InputTaxInvoiceIdempotencyError(
            "only part of the requested evidence set "
            "is already linked to a tax invoice"
        )

    linked_evidence = {
        link.tax_credit_evidence_id
        for link in links
    }

    if linked_evidence != set(evidence_ids):
        raise InputTaxInvoiceIdempotencyError(
            "evidence-link identity mismatch"
        )

    invoice_ids = {
        link.tax_invoice_id
        for link in links
    }

    if len(invoice_ids) != 1:
        raise InputTaxInvoiceIdempotencyError(
            "requested evidence belongs to multiple tax invoices"
        )

    invoice_id = next(
        iter(invoice_ids)
    )

    invoice = await db.scalar(
        select(TaxInvoice)
        .where(
            TaxInvoice.company_id == company_id,
            TaxInvoice.id == invoice_id,
        )
        .with_for_update()
    )

    if invoice is None:
        raise InputTaxInvoiceIdempotencyError(
            "evidence link points to a missing tax invoice"
        )

    return invoice


async def create_input_tax_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    lines: list[InputTaxInvoiceLineAttestation],
    created_by: int,
) -> TaxInvoice:
    """
    Canonicalize one already-attested 10.5 registered supplier PN.

    Monetary VAT facts remain sourced from TaxCreditEvidence /
    TaxCalculation. Legal line details not captured by 10.5
    (description, quantity, UOM, statutory code) are explicitly
    attested here and frozen into TaxInvoiceLine.

    Caller owns commit/rollback.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    lines = validate_input_line_attestations(
        lines
    )

    claim_ids = tuple(
        line.claim_id
        for line in lines
    )

    claims = await _load_claims(
        db,
        company_id=company_id,
        claim_ids=claim_ids,
    )

    attestations = {
        claim_id: _claim_attestation(claim)
        for claim_id, claim in claims.items()
    }

    legal_keys = {
        (
            payload["invoice_number"].strip(),
            _parse_date(
                payload["invoice_date"],
                field="invoice_date",
            ),
            payload["buyer_vat_number"].strip(),
            payload["supplier_vat_number"].strip(),
            _parse_date(
                payload["registered_on"],
                field="registered_on",
            ),
            payload["receipt_reference"].strip(),
        )
        for payload in attestations.values()
    }

    if len(legal_keys) != 1:
        raise InputTaxInvoiceEvidenceError(
            "all claim lines must describe the same registered PN"
        )

    (
        invoice_number,
        invoice_date,
        buyer_vat_number,
        supplier_vat_number,
        registered_on,
        receipt_reference,
    ) = next(
        iter(legal_keys)
    )

    evidence_ids = tuple(
        claims[line.claim_id].evidence_id
        for line in lines
    )

    if len(set(evidence_ids)) != len(evidence_ids):
        raise InputTaxInvoiceEvidenceError(
            "claim lines must use distinct evidence rows"
        )

    existing = await _existing_invoice_for_evidence(
        db,
        company_id=company_id,
        evidence_ids=evidence_ids,
    )

    if existing is not None:
        if (
            existing.direction != "input"
            or existing.source_kind != "input_external"
            or existing.document_number
            != invoice_number
            or existing.document_date
            != invoice_date
        ):
            raise InputTaxInvoiceIdempotencyError(
                "existing evidence links conflict with "
                "requested PN identity"
            )

        existing_lines = list(
            (
                await db.scalars(
                    select(TaxInvoiceLine)
                    .where(
                        TaxInvoiceLine.company_id
                        == company_id,
                        TaxInvoiceLine.tax_invoice_id
                        == existing.id,
                    )
                    .order_by(
                        TaxInvoiceLine.line_number
                    )
                )
            ).all()
        )

        if len(existing_lines) != len(lines):
            raise InputTaxInvoiceIdempotencyError(
                "existing tax invoice has a different line set"
            )

        for persisted, requested in zip(
            existing_lines,
            lines,
            strict=True,
        ):
            if (
                persisted.line_number
                != requested.line_number
                or persisted.description
                != requested.description
                or Decimal(persisted.quantity)
                != Decimal(requested.quantity)
                or persisted.uom_code
                != requested.uom_code
                or persisted.classification_kind
                != requested.classification_kind
                or persisted.statutory_code
                != requested.statutory_code
            ):
                raise InputTaxInvoiceIdempotencyError(
                    "existing tax-invoice snapshot conflicts "
                    "with repeated request"
                )

        return existing

    evidences = {
        item.id: item
        for item in (
            await db.scalars(
                select(TaxCreditEvidence)
                .where(
                    TaxCreditEvidence.company_id
                    == company_id,
                    TaxCreditEvidence.id.in_(
                        evidence_ids
                    ),
                )
                .with_for_update()
            )
        ).all()
    }

    if set(evidences) != set(evidence_ids):
        raise InputTaxInvoiceEvidenceError(
            "tax-credit evidence provenance is incomplete"
        )

    calculation_ids = tuple(
        claims[line.claim_id].tax_calculation_id
        for line in lines
    )

    calculations = {
        item.id: item
        for item in (
            await db.scalars(
                select(TaxCalculation)
                .where(
                    TaxCalculation.company_id
                    == company_id,
                    TaxCalculation.id.in_(
                        calculation_ids
                    ),
                )
            )
        ).all()
    }

    if set(calculations) != set(calculation_ids):
        raise InputTaxInvoiceEvidenceError(
            "tax-calculation provenance is incomplete"
        )

    trade_document_ids = {
        calculation.trade_document_id
        for calculation in calculations.values()
    }

    if len(trade_document_ids) != 1:
        raise InputTaxInvoiceEvidenceError(
            "10.6 INPUT V1 requires one source trade document"
        )

    trade_document_id = next(
        iter(trade_document_ids)
    )

    trade_document = await db.scalar(
        select(TradeDocument).where(
            TradeDocument.company_id == company_id,
            TradeDocument.id == trade_document_id,
        )
    )

    company = await db.scalar(
        select(Company).where(
            Company.id == company_id
        )
    )

    if (
        trade_document is None
        or company is None
    ):
        raise InputTaxInvoiceEvidenceError(
            "INPUT PN company/document provenance is incomplete"
        )

    counterparty = await db.scalar(
        select(Counterparty).where(
            Counterparty.company_id == company_id,
            Counterparty.id
            == trade_document.counterparty_id,
        )
    )

    if counterparty is None:
        raise InputTaxInvoiceEvidenceError(
            "supplier provenance is incomplete"
        )

    for line in lines:
        claim = claims[line.claim_id]
        evidence = evidences[
            claim.evidence_id
        ]
        calculation = calculations[
            claim.tax_calculation_id
        ]

        if claim.reversal_evidence_id is not None:
            raise InputTaxInvoiceEvidenceError(
                "reversed INPUT VAT claim cannot be canonicalized"
            )

        if evidence.reversal_of_id is not None:
            raise InputTaxInvoiceEvidenceError(
                "reversal evidence cannot be used as INPUT PN source"
            )

        if (
            _enum_text(evidence.evidence_type)
            != "registered_tax_invoice"
        ):
            raise InputTaxInvoiceEvidenceError(
                "10.6 INPUT V1 requires REGISTERED_TAX_INVOICE evidence"
            )

        if (
            evidence.tax_calculation_id
            != calculation.id
            or evidence.evidence_number
            != invoice_number
            or evidence.evidence_date
            != invoice_date
        ):
            raise InputTaxInvoiceEvidenceError(
                "10.5 claim/evidence/PN identity mismatch"
            )

        if (
            str(evidence.currency_code).upper()
            != "UAH"
            or str(calculation.currency_code).upper()
            != "UAH"
        ):
            raise InputTaxInvoiceEvidenceError(
                "10.6 INPUT V1 supports UAH only"
            )

        if _enum_text(
            calculation.direction
        ) != "input":
            raise InputTaxInvoiceEvidenceError(
                "INPUT PN cannot use OUTPUT tax calculation"
            )

    seller_tax_number = (
        getattr(counterparty, "tax_number", None)
        or getattr(counterparty, "edrpou", None)
        or supplier_vat_number
    )

    buyer_tax_number = (
        getattr(company, "edrpou", None)
        or getattr(company, "vat_number", None)
        or buyer_vat_number
    )

    tax_invoice = TaxInvoice(
        company_id=company_id,
        direction="input",
        document_number=_required_text(
            invoice_number,
            field="invoice_number",
            max_length=120,
        ),
        document_date=invoice_date,
        currency_code="UAH",
        seller_name=_required_text(
            counterparty.name,
            field="seller_name",
            max_length=255,
        ),
        seller_tax_number=_required_text(
            seller_tax_number,
            field="seller_tax_number",
            max_length=20,
        ),
        seller_vat_number=_required_text(
            supplier_vat_number,
            field="seller_vat_number",
            max_length=20,
        ),
        buyer_name=_required_text(
            company.name,
            field="buyer_name",
            max_length=255,
        ),
        buyer_tax_number=_required_text(
            buyer_tax_number,
            field="buyer_tax_number",
            max_length=20,
        ),
        buyer_vat_number=_required_text(
            buyer_vat_number,
            field="buyer_vat_number",
            max_length=20,
        ),
        source_kind="input_external",
        source_fulfillment_id=None,
        source_payment_settlement_allocation_id=None,
        source_order_vat_advance_id=None,
        created_by=created_by,
    )

    db.add(tax_invoice)
    await db.flush()

    for requested in lines:
        claim = claims[
            requested.claim_id
        ]

        evidence = evidences[
            claim.evidence_id
        ]

        calculation = calculations[
            claim.tax_calculation_id
        ]

        base = Decimal(
            evidence.evidenced_taxable_base
        )
        tax = Decimal(
            evidence.evidenced_tax_amount
        )
        quantity = Decimal(
            requested.quantity
        )

        if (
            not base.is_finite()
            or not tax.is_finite()
            or base < 0
            or tax < 0
        ):
            raise InputTaxInvoiceEvidenceError(
                "INPUT evidence amounts must be finite and nonnegative"
            )

        if base <= 0:
            raise InputTaxInvoiceEvidenceError(
                "10.6 INPUT V1 requires positive taxable base"
            )

        unit_price = (
            base / quantity
        ).quantize(
            Decimal("0.000001"),
            rounding=ROUND_HALF_UP,
        )

        db.add(
            TaxInvoiceLine(
                company_id=company_id,
                tax_invoice_id=tax_invoice.id,
                line_number=requested.line_number,
                tax_calculation_id=calculation.id,
                tax_recognition_event_id=None,
                product_id=calculation.product_id,
                description=requested.description,
                quantity=quantity,
                uom_code=requested.uom_code,
                classification_kind=(
                    requested.classification_kind
                ),
                statutory_code=(
                    requested.statutory_code
                ),
                unit_price_without_vat=unit_price,
                taxable_base=base,
                tax_rate_code=_required_text(
                    calculation.tax_rate_code,
                    field="tax_rate_code",
                    max_length=50,
                ),
                tax_rate=calculation.tax_rate,
                tax_amount=tax,
                total_with_vat=base + tax,
            )
        )

        db.add(
            TaxInvoiceCreditEvidenceLink(
                company_id=company_id,
                tax_invoice_id=tax_invoice.id,
                tax_credit_evidence_id=evidence.id,
                tax_calculation_id=calculation.id,
            )
        )

    await db.flush()

    await append_tax_invoice_registration_event(
        db,
        company_id=company_id,
        tax_invoice_id=tax_invoice.id,
        status="registered",
        event_date=registered_on,
        reference=receipt_reference,
        created_by=created_by,
    )

    return tax_invoice
