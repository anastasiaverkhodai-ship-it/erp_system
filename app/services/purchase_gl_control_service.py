"""Purchase-side economic source plans shared by AP and inventory controls."""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import aliased

from app.models.account import Account
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.input_vat_fulfillment_bridge_event import InputVatFulfillmentBridgeEvent
from app.models.supplier_advance_clearing_event import SupplierAdvanceClearingEvent
from app.models.purchase_return_recognition_event import PurchaseReturnRecognitionEvent
from app.models.purchase_return_vat_adjustment_event import PurchaseReturnVatAdjustmentEvent
from app.models.sales_return_cost_restoration_event import SalesReturnCostRestorationEvent
from app.models.purchase_value_correction_fifo_impact_event import PurchaseValueCorrectionFifoImpactEvent
from app.models.purchase_value_correction_moving_average_replay_event import PurchaseValueCorrectionMovingAverageReplayEvent
from app.models.purchase_value_correction_vat_adjustment_event import PurchaseValueCorrectionVatAdjustmentEvent
from app.models.purchase_landed_cost_valuation_event import PurchaseLandedCostValuationEvent
from app.services.accounting_account_roles import AccountingAccountRole as R
from app.services.event_gl_control_service import EventControlSpec, compare_event_rows, reconcile_event_gl
from app.services.purchase_value_correction_fifo_accounting_service import create_purchase_value_correction_fifo_accounting_plan
from app.services.purchase_value_correction_moving_average_accounting_service import create_purchase_value_correction_moving_average_accounting_plan
from app.services.purchase_value_correction_vat_adjustment_accounting_service import create_purchase_value_correction_vat_adjustment_accounting_plan
from app.services.purchase_value_correction_fifo_issued_destination_resolver import resolve_purchase_value_correction_fifo_issued_destination
from app.services.purchase_value_correction_moving_average_issued_destination_resolver import resolve_purchase_value_correction_moving_average_issued_destination

ZERO=Decimal(0)
SIMPLE_SPECS=(
    EventControlSpec(InputVatFulfillmentBridgeEvent,'input_vat_fulfillment_bridge_event_id','bridge_date',
                     'bridged_tax_amount',R.VAT_INPUT,R.SUPPLIER_PAYABLES),
    EventControlSpec(SupplierAdvanceClearingEvent,'supplier_advance_clearing_event_id','clearing_date',
                     'cleared_amount',R.SUPPLIER_PAYABLES,R.SUPPLIER_ADVANCES),
    EventControlSpec(PurchaseReturnRecognitionEvent,'purchase_return_recognition_event_id','recognition_date',
                     'returned_base_amount',R.SUPPLIER_PAYABLES,R.INVENTORY_GOODS),
    EventControlSpec(PurchaseReturnVatAdjustmentEvent,'purchase_return_vat_adjustment_event_id','adjustment_date',
                     'adjusted_tax_amount',R.SUPPLIER_PAYABLES,R.VAT_INPUT),
    EventControlSpec(SalesReturnCostRestorationEvent,'sales_return_cost_restoration_event_id','restoration_date',
                     'restored_cost_amount',R.INVENTORY_GOODS,R.GOODS_COGS),
)
DYNAMIC_SPECS=(
    EventControlSpec(PurchaseValueCorrectionFifoImpactEvent,'purchase_value_correction_fifo_impact_event_id','recognition_date','amount',None,None),
    EventControlSpec(PurchaseValueCorrectionMovingAverageReplayEvent,'purchase_value_correction_ma_replay_event_id','recognition_date','amount',None,None),
    EventControlSpec(PurchaseValueCorrectionVatAdjustmentEvent,'purchase_value_correction_vat_adjustment_event_id','adjustment_date','amount',None,None),
    EventControlSpec(PurchaseLandedCostValuationEvent,'journal_entry_id','recognition_date','amount',None,None),
)


async def dynamic_plan(db, event, ids):
    if isinstance(event,PurchaseLandedCostValuationEvent):
        account_ids={event.debit_account_id,event.credit_account_id}
        owned=set((await db.scalars(select(Account.id).where(
            Account.company_id==event.company_id,Account.id.in_(account_ids)))).all())
        if len(account_ids)!=2 or owned!=account_ids:
            raise ValueError('Invalid landed-cost source accounts')
        return {event.debit_account_id:[event.amount,ZERO],event.credit_account_id:[ZERO,event.amount]}
    if isinstance(event,PurchaseValueCorrectionVatAdjustmentEvent):
        if event.adjusted_tax_amount==ZERO:
            return {}
        plan=create_purchase_value_correction_vat_adjustment_accounting_plan(
            amount=event.adjusted_tax_amount,adjustment_kind=event.adjustment_kind)
        destination=None
    elif isinstance(event,PurchaseValueCorrectionFifoImpactEvent):
        plan=create_purchase_value_correction_fifo_accounting_plan(original_base_amount=event.original_base_amount,
            corrected_base_amount=event.corrected_base_amount,destination_kind=event.destination_kind)
        destination=(await resolve_purchase_value_correction_fifo_issued_destination(db,event=event,lock=False)).account_id if event.destination_kind=='issued' else None
    else:
        plan=create_purchase_value_correction_moving_average_accounting_plan(original_valuation_amount=event.original_valuation_amount,
            corrected_valuation_amount=event.corrected_valuation_amount,destination_kind=event.effect_kind)
        destination=(await resolve_purchase_value_correction_moving_average_issued_destination(db,event=event,lock=False)).account_id if event.effect_kind=='issued' else None
    result=defaultdict(lambda:[ZERO,ZERO])
    for line in plan.lines:
        account_id=ids[line.role] if line.role is not None else destination
        if account_id is None:
            raise ValueError('Unresolved source account')
        result[account_id][0]+=line.debit.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        result[account_id][1]+=line.credit.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    return dict(result)


async def purchase_event_controls(db, *, company_id, date_from, date_to, ids, focus_role):
    account_id=ids[focus_role]
    credit=focus_role==R.SUPPLIER_PAYABLES
    balance=ZERO
    reports=[]
    for spec in SIMPLE_SPECS:
        if focus_role not in (spec.debit_role,spec.credit_role):
            continue
        report=await reconcile_event_gl(db,spec=spec,company_id=company_id,date_from=date_from,date_to=date_to,
            account_ids=ids,focus_account_id=account_id,normal_credit=credit)
        reports.append(report)
        model=spec.model
        value=getattr(model,spec.amount_field)
        total=Decimal(await db.scalar(select(func.coalesce(func.sum(case((model.reversal_of_id.is_(None),value),else_=-value)),0)).where(
            model.company_id==company_id,getattr(model,spec.date_field)<=date_to)))
        if not total.is_finite():
            raise ValueError('Nonfinite economic source amount')
        sign=1 if (spec.credit_role==focus_role if credit else spec.debit_role==focus_role) else -1
        balance+=sign*total
    for spec in DYNAMIC_SPECS:
        model=spec.model
        linked=model is PurchaseLandedCostValuationEvent
        original_event=aliased(model)
        original_journal=aliased(JournalEntry)
        link=JournalEntry.id==model.journal_entry_id if linked else getattr(JournalEntry,spec.journal_field)==model.id
        original_link=original_journal.id==original_event.journal_entry_id if linked else getattr(original_journal,spec.journal_field)==model.reversal_of_id
        selected=select(model.id).where(model.company_id==company_id,
            or_(getattr(model,spec.date_field)<=date_to,select(JournalEntry.id).where(
                JournalEntry.company_id==company_id,link,JournalEntry.entry_date.between(date_from,date_to)).exists())
        ).order_by(model.id).limit(10001)
        rows=(await db.execute(select(model,JournalEntry,JournalEntryLine,original_journal)
            .outerjoin(JournalEntry,and_(JournalEntry.company_id==company_id,link))
            .outerjoin(JournalEntryLine,JournalEntryLine.journal_entry_id==JournalEntry.id)
            .outerjoin(original_event,and_(original_event.company_id==company_id,original_event.id==model.reversal_of_id))
            .outerjoin(original_journal,and_(original_journal.company_id==company_id,original_link,original_journal.reversal_of_id.is_(None)))
            .where(model.company_id==company_id,model.id.in_(selected))
            .execution_options(populate_existing=True))).all()
        grouped=defaultdict(list)
        for row in rows:
            grouped[row[0].id].append(row)
        if len(grouped)>10000:
            raise ValueError('Economic control exceeds 10000 source events')
        for data in grouped.values():
            event=data[0][0]
            plan=await dynamic_plan(db,event,ids)
            event_date=getattr(event,spec.date_field)
            debit,credit_amount=plan.get(account_id,(ZERO,ZERO))
            delta=credit_amount-debit if credit else debit-credit_amount
            if event.reversal_of_id is not None:
                delta=-delta
            if event_date<=date_to:
                balance+=delta
            source=SimpleNamespace(id=event.id,recognition_date=event_date,
                amount=sum((v[0] for v in plan.values()),ZERO),
                currency_code=getattr(event,'currency_code','UAH'),reversal_of_id=event.reversal_of_id)
            report=compare_event_rows(spec=replace(spec,date_field='recognition_date'),
                rows=[(source,j,l,o) for _,j,l,o in data],account_ids={},planned_lines=plan,
                focus_account_id=account_id,normal_credit=credit,date_from=date_from,date_to=date_to)
            if account_id in plan or any(line is not None and line.account_id==account_id for _,_,line,_ in data):
                reports.append(report)
    return reports,balance
