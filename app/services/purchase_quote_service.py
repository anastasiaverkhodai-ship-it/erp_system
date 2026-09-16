"""Supplier quotations and deterministic purchase comparison; caller owns transaction."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext

from sqlalchemy import select
from app.models.company import Company
from app.models.contract import Contract
from app.models.counterparty import Counterparty
from app.models.product import Product
from app.models.purchase_quote import PurchaseQuote
from app.schemas.purchase_quote import (
    PurchaseQuoteCreate, PurchaseQuoteComparisonRequest, PurchaseQuoteComparisonResponse,
    RankedPurchaseQuote, RejectedPurchaseQuote,
)
from app.services.trade_document_types import TradeDirection
from app.services.trade_document_validation import validate_trade_document_contract, TradeDocumentValidationError


class PurchaseQuoteError(ValueError):
    pass


async def _company_product(db, company_id, product_id):
    company = await db.scalar(select(Company).where(Company.id == company_id, Company.is_active.is_(True)))
    product = await db.scalar(select(Product).where(Product.id == product_id, Product.company_id == company_id,
                                                    Product.is_active.is_(True)))
    if company is None or product is None:
        raise PurchaseQuoteError("Active company/product not found")


def supplier_contract_errors(supplier, contract, *, company_id, supplier_id, contract_id, operation_date, currency_code):
    reasons = []
    if supplier is None or supplier.company_id != company_id or not supplier.is_active:
        reasons.append("supplier_inactive_or_missing")
    elif supplier.counterparty_type not in {"supplier", "both"}:
        reasons.append("counterparty_is_not_supplier")
    if contract_id is not None:
        if contract is None:
            reasons.append("contract_missing")
        else:
            try:
                validate_trade_document_contract(contract=contract, company_id=company_id, counterparty_id=supplier_id,
                    direction=TradeDirection.PURCHASE, document_date=operation_date, currency_code=currency_code)
            except TradeDocumentValidationError as exc:
                reasons.append(str(exc))
    return reasons


async def create_purchase_quote(db, *, company_id, data: PurchaseQuoteCreate, created_by):
    # Validate direct service calls with the same contract as HTTP requests.
    data = PurchaseQuoteCreate.model_validate(data)
    await _company_product(db, company_id, data.product_id)
    supplier = await db.scalar(select(Counterparty).where(Counterparty.company_id == company_id,
                                                         Counterparty.id == data.supplier_id).with_for_update().execution_options(populate_existing=True))
    contract = None
    if data.contract_id is not None:
        contract = await db.scalar(select(Contract).where(Contract.company_id == company_id,
                                                         Contract.id == data.contract_id).with_for_update().execution_options(populate_existing=True))
    reasons = supplier_contract_errors(supplier, contract, company_id=company_id, supplier_id=data.supplier_id,
        contract_id=data.contract_id, operation_date=data.valid_from, currency_code=data.currency_code)
    if reasons:
        raise PurchaseQuoteError("; ".join(reasons))
    previous = await db.scalar(select(PurchaseQuote).where(PurchaseQuote.company_id == company_id,
        PurchaseQuote.supplier_id == data.supplier_id, PurchaseQuote.product_id == data.product_id,
        PurchaseQuote.reference == data.reference).execution_options(populate_existing=True))
    if previous is not None:
        if any(getattr(previous, name) != value for name, value in data.model_dump().items()):
            raise PurchaseQuoteError("Reference already exists with different terms; use a new reference for a revised offer")
        return previous
    quote = PurchaseQuote(company_id=company_id, created_by=created_by, **data.model_dump())
    db.add(quote)
    await db.flush()
    return quote


async def withdraw_purchase_quote(db, *, company_id, quote_id, withdrawn_by):
    quote = await db.scalar(select(PurchaseQuote).where(PurchaseQuote.company_id == company_id,
        PurchaseQuote.id == quote_id).with_for_update().execution_options(populate_existing=True))
    if quote is None:
        raise PurchaseQuoteError("Quote not found in this company")
    if quote.is_active:
        quote.is_active = False
        quote.withdrawn_by = withdrawn_by
        quote.withdrawn_at = datetime.now(timezone.utc)
        await db.flush()
    return quote


def rank_purchase_quotes(*, company_id, request: PurchaseQuoteComparisonRequest, quotes, suppliers, contracts):
    """Compare quoted payable amounts, with no tax-rate inference or FX conversion."""
    ranked, rejected = [], []
    for quote in quotes:
        if quote.company_id != company_id or quote.product_id != request.product_id:
            continue  # Never disclose foreign-company or unrelated offers.
        supplier = suppliers.get(quote.supplier_id)
        reasons = supplier_contract_errors(supplier, contracts.get(quote.contract_id), company_id=company_id,
            supplier_id=quote.supplier_id, contract_id=quote.contract_id, operation_date=request.order_date,
            currency_code=request.currency_code)
        if not quote.is_active: reasons.append("quote_withdrawn")
        if not quote.valid_from <= request.order_date <= quote.valid_until: reasons.append("quote_not_valid_on_order_date")
        if quote.currency_code != request.currency_code: reasons.append("currency_mismatch")
        if request.quantity < quote.min_quantity or (quote.max_quantity is not None and request.quantity > quote.max_quantity):
            reasons.append("quantity_outside_offer_range")
        try:
            delivery_date = request.order_date + timedelta(days=quote.lead_time_days)
        except OverflowError:
            reasons.append("delivery_date_out_of_range")
            delivery_date = None
        if delivery_date and request.required_delivery_date and delivery_date > request.required_delivery_date:
            reasons.append("delivery_after_deadline")
        with localcontext() as ctx:
            ctx.prec = 50
            goods_net = (request.quantity * quote.unit_price_net).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            goods_gross = (request.quantity * quote.unit_price_gross).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_net = goods_net + quote.delivery_net
            total_payable = goods_gross + quote.delivery_gross
        if total_payable >= Decimal("1e16"):
            reasons.append("amount_exceeds_supported_range")
        if reasons:
            rejected.append(RejectedPurchaseQuote(quote_id=quote.id, reasons=reasons))
            continue
        ranked.append(RankedPurchaseQuote(quote_id=quote.id, supplier_id=quote.supplier_id,
            supplier_name=supplier.name, reference=quote.reference, contract_id=quote.contract_id,
            goods_net=goods_net, goods_gross=goods_gross, delivery_net=quote.delivery_net,
            delivery_gross=quote.delivery_gross, total_net=total_net, total_payable=total_payable,
            total_vat=total_payable-total_net, delivery_date=delivery_date, payment_term_days=quote.payment_term_days))
    ranked.sort(key=lambda item: (item.total_payable, item.delivery_date, item.quote_id))
    rejected.sort(key=lambda item: item.quote_id)
    return PurchaseQuoteComparisonResponse(product_id=request.product_id, quantity=request.quantity,
        recommended_quote_id=ranked[0].quote_id if ranked else None, ranked=ranked, rejected=rejected)


async def compare_purchase_quotes(db, *, company_id, request: PurchaseQuoteComparisonRequest):
    request = PurchaseQuoteComparisonRequest.model_validate(request)
    await _company_product(db, company_id, request.product_id)
    # One statement reads offers and mutable eligibility references together.
    # The comparison is a recommendation snapshot, not an order or reservation.
    rows = (await db.execute(select(PurchaseQuote, Counterparty, Contract)
        .join(Counterparty, (Counterparty.id == PurchaseQuote.supplier_id) & (Counterparty.company_id == PurchaseQuote.company_id))
        .outerjoin(Contract, (Contract.id == PurchaseQuote.contract_id) & (Contract.company_id == PurchaseQuote.company_id))
        .where(PurchaseQuote.company_id == company_id, PurchaseQuote.product_id == request.product_id)
        .order_by(PurchaseQuote.id))).all()
    return rank_purchase_quotes(company_id=company_id, request=request, quotes=[q for q, _, _ in rows],
        suppliers={s.id: s for _, s, _ in rows}, contracts={c.id: c for _, _, c in rows if c is not None})
