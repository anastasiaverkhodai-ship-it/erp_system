"""Check dated registration settings against actual first-event dates.

The immutable calculation remains a single-rate pool. Unsupported cross-regime
histories fail before recognition writes; they are never re-priced silently.
"""
from sqlalchemy import select
from app.models.trade_document import TradeDocument
from app.models.company_vat_policy import CompanyVatPolicy
from app.models.counterparty_vat_registration import CounterpartyVatRegistration
from app.services.tax_recognition_orchestration_service import build_economic_recognition_timeline
from app.services.tax_recognition_persistence_service import TaxRecognitionDataIntegrityError
from app.services.tax_recognition_types import TaxRecognitionMethod


async def validate_first_event_context(db, *, calculation, candidates, invoice=None):
    if not candidates:
        return
    if invoice is None:
        invoice = await db.scalar(select(TradeDocument).where(
            TradeDocument.company_id == calculation.company_id,
            TradeDocument.id == calculation.trade_document_id,
        ))
    if invoice is None:
        raise TaxRecognitionDataIntegrityError('First-event invoice source not found')
    if invoice.kind == 'invoice':
        from app.services.order_vat_advance_service import assert_invoice_has_no_pending_advances, OrderVatAdvanceError
        try:
            await assert_invoice_has_no_pending_advances(db, invoice=invoice)
        except OrderVatAdvanceError as exc:
            raise TaxRecognitionDataIntegrityError(str(exc)) from exc
    if invoice.vat_policy_id is None:
        return  # Historical legacy snapshot; strict settings were not attested.
    snapshot = await db.get(CompanyVatPolicy, invoice.vat_policy_id)
    party_snapshot = await db.get(CounterpartyVatRegistration, invoice.counterparty_vat_registration_id)
    if snapshot is None or party_snapshot is None:
        raise TaxRecognitionDataIntegrityError('First-event registration snapshot not found')
    timeline = build_economic_recognition_timeline(candidates=candidates,
        calculated_base=calculation.taxable_base, calculated_tax=calculation.tax_amount)
    for day in sorted({item.event_date for item in timeline}):
        company = await db.scalar(select(CompanyVatPolicy).where(
            CompanyVatPolicy.company_id == calculation.company_id, CompanyVatPolicy.effective_from <= day,
        ).order_by(CompanyVatPolicy.effective_from.desc()).limit(1))
        party = await db.scalar(select(CounterpartyVatRegistration).where(
            CounterpartyVatRegistration.company_id == calculation.company_id,
            CounterpartyVatRegistration.counterparty_id == invoice.counterparty_id,
            CounterpartyVatRegistration.effective_from <= day,
        ).order_by(CounterpartyVatRegistration.effective_from.desc()).limit(1))
        if company is None or party is None:
            raise TaxRecognitionDataIntegrityError('Registration evidence is missing on first-event date')
        if (company.payer_status, company.vat_number, party.payer_status, party.vat_number) != (
            snapshot.payer_status, snapshot.vat_number, party_snapshot.payer_status, party_snapshot.vat_number,
        ):
            raise TaxRecognitionDataIntegrityError('First-event registration differs from invoice snapshot; separate tax treatment required')
        if calculation.recognition_method == TaxRecognitionMethod.CASH_METHOD and not company.allow_cash_method:
            raise TaxRecognitionDataIntegrityError('Cash method was not authorized on first-event date')
