"""Evidence-gated INPUT advance capacity and atomic order-to-invoice credit handoff."""
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from app.models.order_vat_advance import OrderVatAdvance, OrderVatAdvanceLine
from app.models.payment import Payment
from app.models.tax_calculation import TaxCalculation
from app.models.trade_document_line import TradeDocumentLine
from app.models.tax_credit_evidence import TaxCreditEvidence
from app.models.input_vat_credit_claim import InputVatCreditClaim
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.models.tax_invoice_credit_evidence_link import TaxInvoiceCreditEvidenceLink
from app.services.tax_recognition_orchestration_service import TaxRecognitionCandidate, TaxRecognitionCandidateKind


async def load_order_credit_candidates(db, *, calculation, order):
    from app.services.input_tax_recognition_candidate_loader_service import InputTaxRecognitionCandidateLoaderStateError
    from app.services.vat_first_event_context_service import validate_first_event_context
    if (order.direction!='purchase' or order.status not in ('confirmed','partially_fulfilled','fulfilled')
            or order.currency_code!='UAH' or calculation.direction!='input' or calculation.recognition_method!='first_event'):
        raise InputTaxRecognitionCandidateLoaderStateError('Order credit requires confirmed UAH purchase FIRST_EVENT terms')
    records=(await db.execute(select(OrderVatAdvance,OrderVatAdvanceLine,Payment).join(OrderVatAdvanceLine,
        (OrderVatAdvanceLine.company_id==OrderVatAdvance.company_id)&(OrderVatAdvanceLine.advance_id==OrderVatAdvance.id))
        .join(Payment,(Payment.company_id==OrderVatAdvance.company_id)&(Payment.id==OrderVatAdvance.payment_id))
        .where(OrderVatAdvance.company_id==calculation.company_id,OrderVatAdvance.order_id==order.id,
               OrderVatAdvance.status=='active',OrderVatAdvanceLine.tax_calculation_id==calculation.id)
        .order_by(OrderVatAdvance.event_date,OrderVatAdvance.id))).all()
    candidates=[]
    for advance,line,payment in records:
        if payment.status!='confirmed' or payment.direction!='outgoing' or payment.counterparty_id!=order.counterparty_id or payment.currency_code!='UAH':
            raise InputTaxRecognitionCandidateLoaderStateError('Order advance payment is not valid for INPUT credit')
        candidates.append(TaxRecognitionCandidate(kind=TaxRecognitionCandidateKind.SETTLEMENT,
            source_id=advance.id,event_date=advance.event_date,taxable_base_capacity=line.base,tax_amount_capacity=line.tax))
    await validate_first_event_context(db,calculation=calculation,candidates=candidates,invoice=order)
    return tuple(candidates)


async def prepare_order_credit_transfer(db, *, company_id, order, invoice, created_by):
    from app.services.input_vat_credit_claim_service import InputVatCreditClaimError
    from app.services.tax_credit_evidence_lifecycle_service import reverse_tax_credit_evidence_and_reconcile
    claims=list((await db.scalars(select(InputVatCreditClaim).join(TaxCalculation,
        (TaxCalculation.company_id==InputVatCreditClaim.company_id)&(TaxCalculation.id==InputVatCreditClaim.tax_calculation_id))
        .where(InputVatCreditClaim.company_id==company_id,TaxCalculation.trade_document_id==order.id,
            InputVatCreditClaim.reversal_evidence_id.is_(None)).order_by(InputVatCreditClaim.id).with_for_update())).all())
    if not claims:
        return []
    pending=[]
    for claim in claims:
        evidence=await db.get(TaxCreditEvidence,claim.evidence_id)
        calc=await db.get(TaxCalculation,claim.tax_calculation_id)
        # Transfer already requires identical numbered order/invoice lines.
        number=await db.scalar(select(TradeDocumentLine.line_number).where(TradeDocumentLine.company_id==company_id,TradeDocumentLine.id==calc.trade_document_line_id))
        targets=list((await db.scalars(select(TaxCalculation).join(TradeDocumentLine,(TradeDocumentLine.company_id==TaxCalculation.company_id)&(TradeDocumentLine.id==TaxCalculation.trade_document_line_id)).where(TradeDocumentLine.line_number==number,TaxCalculation.company_id==company_id,
            TaxCalculation.trade_document_id==invoice.id,TaxCalculation.product_id==calc.product_id,
            TaxCalculation.tax_rate_code==calc.tax_rate_code,TaxCalculation.taxable_base==calc.taxable_base,
            TaxCalculation.tax_amount==calc.tax_amount))).all())
        if len(targets)!=1:
            raise InputVatCreditClaimError('Credit transfer requires a unique matching invoice tax calculation')
        latest=await db.scalar(select(TaxRecognitionEvent.recognition_date).where(
            TaxRecognitionEvent.company_id==company_id,TaxRecognitionEvent.tax_calculation_id==calc.id)
            .order_by(TaxRecognitionEvent.recognition_date.desc()).limit(1))
        effective=max(invoice.document_date,latest or invoice.document_date)
        if effective>date.today():
            raise InputVatCreditClaimError('Future credit transfer is not supported')
        result=await reverse_tax_credit_evidence_and_reconcile(db,company_id=company_id,evidence_id=evidence.id,
            reversal_date=effective,reversed_by=created_by)
        claim.reversal_evidence_id=result.evidence.id
        pending.append((claim,evidence,targets[0],effective))
    await db.flush()
    return pending


async def finish_order_credit_transfer(db, *, company_id, pending, created_by):
    from app.services.tax_credit_evidence_lifecycle_service import create_tax_credit_evidence_and_reconcile
    from app.services.tax_credit_evidence_types import TaxCreditEvidenceType
    for claim,evidence,target,effective in pending:
        result=await create_tax_credit_evidence_and_reconcile(db,company_id=company_id,tax_calculation_id=target.id,
            evidence_type=TaxCreditEvidenceType.REGISTERED_TAX_INVOICE,evidence_number=evidence.evidence_number,
            evidence_date=evidence.evidence_date,credit_available_date=effective,
            evidenced_taxable_base=evidence.evidenced_taxable_base,evidenced_tax_amount=evidence.evidenced_tax_amount,
            currency_code='UAH',adjustment_date=effective,created_by=created_by)
        new_claim=InputVatCreditClaim(company_id=company_id,tax_calculation_id=target.id,evidence_id=result.evidence.id,
            request_key=f'advance-credit-transfer:{claim.id}:{target.id}',policy_version=claim.policy_version,
            claim_period=effective.replace(day=1),attestation={**claim.attestation,'tax_calculation_id':target.id},
            decision={**claim.decision,'transfer_from_claim_id':claim.id,'transfer_effective_date':effective.isoformat()},created_by=created_by)
        db.add(new_claim)
        links=list((await db.scalars(select(TaxInvoiceCreditEvidenceLink).where(
            TaxInvoiceCreditEvidenceLink.company_id==company_id,TaxInvoiceCreditEvidenceLink.tax_credit_evidence_id==evidence.id))).all())
        for link in links:
            db.add(TaxInvoiceCreditEvidenceLink(company_id=company_id,tax_invoice_id=link.tax_invoice_id,
                tax_credit_evidence_id=result.evidence.id,tax_calculation_id=target.id))
    await db.flush()
