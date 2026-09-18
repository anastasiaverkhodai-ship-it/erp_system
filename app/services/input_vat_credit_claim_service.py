"""Attested PN -> policy-selected period -> economic capacity -> immutable credit/GL."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select
from app.models.input_vat_credit_claim import InputVatCreditClaim
from app.models.tax_calculation import TaxCalculation
from app.models.tax_credit_evidence import TaxCreditEvidence
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.models.trade_document import TradeDocument
from app.models.company_vat_policy import CompanyVatPolicy
from app.models.counterparty_vat_registration import CounterpartyVatRegistration
from app.services.input_vat_credit_eligibility_service import assess_input_vat_credit, month_end
from app.services.input_tax_recognition_candidate_loader_service import load_active_input_tax_recognition_candidates
from app.services.input_tax_recognition_calculation_service import calculate_input_tax_recognition_limit
from app.services.tax_credit_evidence_lifecycle_service import create_tax_credit_evidence_and_reconcile, reverse_tax_credit_evidence_and_reconcile
from app.services.tax_credit_evidence_types import TaxCreditEvidenceType
from app.services.accounting_period_service import ensure_period_open


class InputVatCreditClaimError(ValueError):
    pass


async def _locked_calculation(db, company_id, calculation_id):
    document_id = await db.scalar(select(TaxCalculation.trade_document_id).where(
        TaxCalculation.company_id == company_id, TaxCalculation.id == calculation_id))
    if document_id is None:
        raise InputVatCreditClaimError('INPUT calculation not found in company')
    invoice = await db.scalar(select(TradeDocument).where(TradeDocument.company_id == company_id,
        TradeDocument.id == document_id).with_for_update().execution_options(populate_existing=True))
    calculation = await db.scalar(select(TaxCalculation).where(TaxCalculation.company_id == company_id,
        TaxCalculation.id == calculation_id).with_for_update().execution_options(populate_existing=True))
    if invoice.kind not in ('invoice','order') or invoice.direction != 'purchase' or invoice.status not in (('confirmed','partially_fulfilled','fulfilled') if invoice.kind=='order' else ('confirmed',)):
        raise InputVatCreditClaimError('Confirmed purchase invoice or order required')
    if calculation.direction != 'input' or calculation.currency_code != 'UAH' or calculation.recognition_method != 'first_event':
        raise InputVatCreditClaimError('This policy supports INPUT UAH FIRST_EVENT only')
    return calculation, invoice


async def _validate_registration_parties(db, company_id, invoice, data, credit_date):
    company = await db.scalar(select(CompanyVatPolicy).where(CompanyVatPolicy.company_id == company_id,
        CompanyVatPolicy.effective_from <= data.invoice_date).order_by(CompanyVatPolicy.effective_from.desc()).limit(1))
    supplier = await db.scalar(select(CounterpartyVatRegistration).where(
        CounterpartyVatRegistration.company_id == company_id,
        CounterpartyVatRegistration.counterparty_id == invoice.counterparty_id,
        CounterpartyVatRegistration.effective_from <= data.invoice_date)
        .order_by(CounterpartyVatRegistration.effective_from.desc()).limit(1))
    if company is None or supplier is None or company.payer_status != 'vat_payer' or supplier.payer_status != 'vat_payer':
        raise InputVatCreditClaimError('Dated buyer and supplier VAT-payer evidence required on PN date')
    if (company.vat_number, supplier.vat_number) != (data.buyer_vat_number, data.supplier_vat_number):
        raise InputVatCreditClaimError('PN VAT numbers differ from dated buyer/supplier registrations')
    claim_policy = await db.scalar(select(CompanyVatPolicy).where(CompanyVatPolicy.company_id == company_id,
        CompanyVatPolicy.effective_from <= credit_date).order_by(CompanyVatPolicy.effective_from.desc()).limit(1))
    if claim_policy is None or claim_policy.payer_status != 'vat_payer' or claim_policy.vat_number != data.buyer_vat_number:
        raise InputVatCreditClaimError('Buyer is not the attested VAT payer on credit date')
    if invoice.vat_policy_id is None or invoice.counterparty_vat_registration_id is None:
        raise InputVatCreditClaimError('Invoice requires validated VAT registration snapshots; legacy settings need explicit migration')


async def create_input_vat_credit_claim(db, *, company_id, data, created_by):
    if created_by <= 0:
        raise InputVatCreditClaimError('Invalid actor')
    from app.services.order_vat_advance_service import lock_party
    party_id=await db.scalar(select(TradeDocument.counterparty_id).join(TaxCalculation,
        (TaxCalculation.company_id==TradeDocument.company_id)&(TaxCalculation.trade_document_id==TradeDocument.id))
        .where(TaxCalculation.company_id==company_id,TaxCalculation.id==data.tax_calculation_id))
    if party_id is None:
        raise InputVatCreditClaimError('INPUT calculation not found in company')
    await lock_party(db,company_id,party_id)
    # Advance cancellation locks payments before order/calculation rows. Use the
    # same order so a claim cannot race a cancellation or deadlock a handoff.
    from app.models.order_vat_advance import OrderVatAdvance
    from app.models.payment import Payment
    document_id=await db.scalar(select(TaxCalculation.trade_document_id).where(
        TaxCalculation.company_id==company_id,TaxCalculation.id==data.tax_calculation_id))
    payment_ids=list((await db.scalars(select(OrderVatAdvance.payment_id).where(
        OrderVatAdvance.company_id==company_id,OrderVatAdvance.order_id==document_id,
        OrderVatAdvance.status=='active').order_by(OrderVatAdvance.payment_id))).all())
    if payment_ids:
        await db.execute(select(Payment.id).where(Payment.company_id==company_id,Payment.id.in_(payment_ids)).order_by(Payment.id).with_for_update())

    calculation, invoice = await _locked_calculation(db, company_id, data.tax_calculation_id)
    payload = data.model_dump(mode='json')
    existing = await db.scalar(select(InputVatCreditClaim).where(InputVatCreditClaim.company_id == company_id,
        InputVatCreditClaim.request_key == data.request_key))
    if existing is not None:
        if existing.attestation != payload:
            raise InputVatCreditClaimError('Request key already used with different data')
        return existing
    today = date.today()
    decision = assess_input_vat_credit(data, as_of_date=today)
    if not decision.eligible:
        raise InputVatCreditClaimError(decision.reason)
    await _validate_registration_parties(db, company_id, invoice, data, decision.credit_available_date)
    expected_tax = (data.taxable_base * calculation.tax_rate).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    if abs(expected_tax - data.tax_amount) > Decimal('.01'):
        raise InputVatCreditClaimError('Attested base/tax does not match the calculation rate')
    as_of = min(today, month_end(data.claim_period))
    if await db.scalar(select(TaxRecognitionEvent.id).where(TaxRecognitionEvent.company_id == company_id,
        TaxRecognitionEvent.tax_calculation_id == calculation.id, TaxRecognitionEvent.recognition_date > as_of).limit(1)):
        raise InputVatCreditClaimError('Later recognition history exists; historical period needs an explicit reviewed correction')
    candidates = await load_active_input_tax_recognition_candidates(db, calculation=calculation)
    history = list((await db.scalars(select(TaxCreditEvidence).where(TaxCreditEvidence.company_id == company_id,
        TaxCreditEvidence.tax_calculation_id == calculation.id).order_by(TaxCreditEvidence.id))).all())
    if any(e.effective_date > as_of for e in history):
        raise InputVatCreditClaimError('Later legal evidence exists; historical claim needs a reviewed correction')
    limit = calculate_input_tax_recognition_limit(calculation=calculation, economic_candidates=candidates,
        evidence_events=history, as_of_date=as_of)
    if (limit.evidence_taxable_base + data.taxable_base > limit.economic_taxable_base or
            limit.evidence_tax_amount + data.tax_amount > limit.economic_tax_amount):
        raise InputVatCreditClaimError('Claim exceeds unclaimed economic capacity in selected period')
    await ensure_period_open(company_id=company_id, operation_date=decision.credit_available_date, db=db)
    result = await create_tax_credit_evidence_and_reconcile(db, company_id=company_id,
        tax_calculation_id=calculation.id, evidence_type=TaxCreditEvidenceType.REGISTERED_TAX_INVOICE,
        evidence_number=data.invoice_number, evidence_date=data.invoice_date,
        credit_available_date=decision.credit_available_date, evidenced_taxable_base=data.taxable_base,
        evidenced_tax_amount=data.tax_amount, currency_code='UAH', adjustment_date=as_of, created_by=created_by)
    claim = InputVatCreditClaim(company_id=company_id, tax_calculation_id=calculation.id,
        evidence_id=result.evidence.id, request_key=data.request_key, policy_version=decision.policy_version,
        claim_period=data.claim_period, attestation=payload, decision=decision.model_dump(mode='json'), created_by=created_by)
    db.add(claim); await db.flush()
    return claim


async def reverse_input_vat_credit_claim(db, *, company_id, claim_id, reversal_date, reversed_by):
    identity = await db.scalar(select(InputVatCreditClaim).where(InputVatCreditClaim.company_id == company_id,
        InputVatCreditClaim.id == claim_id))
    if identity is None or reversed_by <= 0:
        raise InputVatCreditClaimError('Claim or actor not found')
    await _locked_calculation(db, company_id, identity.tax_calculation_id)
    claim = await db.scalar(select(InputVatCreditClaim).where(InputVatCreditClaim.company_id == company_id,
        InputVatCreditClaim.id == claim_id).with_for_update().execution_options(populate_existing=True))
    if claim.reversal_evidence_id is not None:
        reversal = await db.get(TaxCreditEvidence, claim.reversal_evidence_id)
        if reversal.effective_date != reversal_date:
            raise InputVatCreditClaimError('Claim already reversed on another date')
        return claim
    if reversal_date > date.today():
        raise InputVatCreditClaimError('Future reversal is not supported')
    latest = await db.scalar(select(TaxRecognitionEvent.recognition_date).where(
        TaxRecognitionEvent.company_id == company_id, TaxRecognitionEvent.tax_calculation_id == claim.tax_calculation_id)
        .order_by(TaxRecognitionEvent.recognition_date.desc()).limit(1))
    if latest and reversal_date < latest:
        raise InputVatCreditClaimError('Reversal cannot precede later recognition history')
    await ensure_period_open(company_id=company_id, operation_date=reversal_date, db=db)
    result = await reverse_tax_credit_evidence_and_reconcile(db, company_id=company_id,
        evidence_id=claim.evidence_id, reversal_date=reversal_date, reversed_by=reversed_by)
    claim.reversal_evidence_id = result.evidence.id
    await db.flush()
    return claim
