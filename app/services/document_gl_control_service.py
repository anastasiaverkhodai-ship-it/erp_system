"""Reconstruct warehouse-document journals from configured rules and source values.

No GL amount is used to manufacture an expected amount. Historical issue cost
comes from InventoryCostEntry, receipt values from immutable posted lines.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload

from app.models.document import Document
from app.models.accounting_rule import AccountingRule
from app.models.account import Account
from app.models.inventory_cost_entry import InventoryCostEntry
from app.models.journal_entry import JournalEntry
from app.models.stock_ledger import StockLedger
from app.models.warehouse_transfer_line import WarehouseTransferLine
from app.services.event_gl_control_service import EventControlSpec, EventGlIssue, compare_event_rows

ZERO = Decimal(0)


def document_plan(document, rule, costs):
    """The same monetary contract as document_accounting, without any writes."""
    if rule is None or rule.company_id != document.company_id or rule.document_type != document.document_type:
        raise ValueError("missing_or_incompatible_accounting_rule")
    if not rule.lines or not document.lines:
        raise ValueError("empty_document_or_rule")
    total=sum(((line.quantity*line.price).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
               for line in document.lines),ZERO)
    cost_total=ZERO
    if any(line.amount_source=='inventory_cost' for line in rule.lines):
        for line in document.lines:
            cost=costs.get(line.id)
            if cost is None or cost.quantity != line.quantity:
                raise ValueError("missing_or_invalid_inventory_cost")
            cost_total += cost.cost_amount.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    plan=defaultdict(lambda:[ZERO,ZERO])
    for line in rule.lines:
        if line.amount_source in ('document_total','line_total'):
            amount=total
        elif line.amount_source=='inventory_cost':
            amount=cost_total
        else:
            raise ValueError("unsupported_accounting_amount_source")
        if not amount.is_finite() or amount < ZERO:
            raise ValueError("invalid_document_amount")
        if line.side not in ('debit','credit'):
            raise ValueError("invalid_accounting_side")
        plan[line.account_id][0 if line.side=='debit' else 1] += amount
    if sum((v[0] for v in plan.values()),ZERO) != sum((v[1] for v in plan.values()),ZERO):
        raise ValueError("unbalanced_source_plan")
    return dict(plan)


async def reconcile_documents_gl(db, *, company_id, date_from, date_to, focus_account_id, normal_credit=False, require_mapping=True):
    documents=(await db.scalars(select(Document).options(selectinload(Document.lines)).where(
        Document.company_id==company_id, Document.status.in_(('posted','reversed')),
        or_(Document.document_date<=date_to, select(JournalEntry.id).where(
            JournalEntry.company_id==company_id,JournalEntry.document_id==Document.id,
            JournalEntry.entry_date.between(date_from,date_to)).exists())
    ).order_by(Document.id).limit(10001).execution_options(populate_existing=True))).all()
    if len(documents)>10000:
        raise ValueError("Document control exceeds 10000 source documents")
    if not documents:
        return [],ZERO
    document_ids=[d.id for d in documents]
    transfer_pairs=(await db.execute(select(
        WarehouseTransferLine.issue_document_id, WarehouseTransferLine.receipt_document_id
    ).where(WarehouseTransferLine.company_id==company_id, or_(
        WarehouseTransferLine.issue_document_id.in_(document_ids),
        WarehouseTransferLine.receipt_document_id.in_(document_ids)
    )))).all()
    transfer_document_ids={document_id for pair in transfer_pairs for document_id in pair}
    from app.models.opening_balance_detail import OpeningBalanceDetail
    opening_document_ids=set((await db.scalars(select(OpeningBalanceDetail.stock_document_id).where(
        OpeningBalanceDetail.company_id==company_id,
        OpeningBalanceDetail.stock_document_id.in_(document_ids)))).all())
    physical_only_ids=transfer_document_ids | opening_document_ids
    rules=(await db.scalars(select(AccountingRule).options(selectinload(AccountingRule.lines)).where(
        AccountingRule.company_id==company_id,or_(
            AccountingRule.id.in_({d.accounting_rule_id for d in documents if d.accounting_rule_id is not None}),
            AccountingRule.id.in_(select(JournalEntry.accounting_rule_id).where(
                JournalEntry.company_id==company_id,JournalEntry.document_id.in_(document_ids),
                JournalEntry.reversal_of_id.is_(None))))
    ).execution_options(populate_existing=True))).all()
    rule_by_id={r.id:r for r in rules}
    allowed_accounts=set((await db.scalars(select(Account.id).where(Account.company_id==company_id))).all())
    costs=(await db.scalars(select(InventoryCostEntry).where(InventoryCostEntry.company_id==company_id,
        InventoryCostEntry.document_id.in_(document_ids)).execution_options(populate_existing=True))).all()
    cost_by_line={c.document_line_id:c for c in costs}
    entries=(await db.scalars(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.company_id==company_id,JournalEntry.document_id.in_(document_ids)
    ).execution_options(populate_existing=True))).all()
    entries_by_document=defaultdict(list)
    for entry in entries:
        entries_by_document[entry.document_id].append(entry)
    reversal_dates=defaultdict(set)
    for document_id,movement_date in (await db.execute(select(StockLedger.document_id,StockLedger.movement_date).where(
        StockLedger.company_id==company_id,StockLedger.document_id.in_(document_ids),StockLedger.movement_type=='reversal'
    ).distinct())).all():
        reversal_dates[document_id].add(movement_date)
    reports=[]
    balance=ZERO
    for document in documents:
        errors=[]
        journals=entries_by_document[document.id]
        originals=[j for j in journals if j.reversal_of_id is None]
        original=originals[0] if len(originals)==1 else None
        # Older documents persisted rule provenance only on their original journal.
        # Recover the rule identity, never expected amounts, from that immutable link.
        rule_id=document.accounting_rule_id or (original.accounting_rule_id if original else None)
        try:
            if document.accounting_rule_id is not None and original is not None and original.accounting_rule_id != document.accounting_rule_id:
                raise ValueError('document_journal_rule_mismatch')
            # Internal transfers preserve company-wide inventory value and intentionally
            # have no journal. Only typed transfer provenance authorizes this exception.
            plan={} if document.id in physical_only_ids else document_plan(document,rule_by_id.get(rule_id),cost_by_line)
            if set(plan)-allowed_accounts:
                raise ValueError("foreign_account_in_source_rule")
        except ValueError as exc:
            plan={}
            errors.append(str(exc))
        debit,credit=plan.get(focus_account_id,(ZERO,ZERO))
        sign_amount=credit-debit if normal_credit else debit-credit
        if document.document_date<=date_to:
            balance+=sign_amount
        journals=entries_by_document[document.id]
        originals=[j for j in journals if j.reversal_of_id is None]
        original=originals[0] if len(originals)==1 else None
        for reversal in (False,True):
            applicable=not reversal or document.status=='reversed'
            matching=[j for j in journals if (j.reversal_of_id is not None)==reversal]
            if not applicable and not matching:
                continue
            dates=reversal_dates[document.id]
            if reversal and applicable and len(dates)!=1:
                errors.append('missing_or_ambiguous_stock_reversal_date')
            event_date=next(iter(dates)) if reversal and len(dates)==1 else document.document_date
            if reversal and applicable and event_date<=date_to:
                balance-=sign_amount
            event=SimpleNamespace(id=document.id,currency_code='UAH',recognition_date=event_date,
                amount=sum((v[0] for v in plan.values()),ZERO) if applicable else ZERO,
                reversal_of_id=document.id if reversal else None)
            rows=[]
            for entry in matching:
                for line in entry.lines or [None]:
                    rows.append((event,entry,line,original if reversal else None))
            if not rows:
                rows=[(event,None,None,original if reversal else None)]
            report=compare_event_rows(spec=EventControlSpec(Document,'document_id','recognition_date','amount',None,None),
                rows=rows,account_ids={},focus_account_id=focus_account_id,normal_credit=normal_credit,
                date_from=date_from,date_to=date_to,planned_lines=plan if applicable else {})
            for code in errors:
                report.issues.append(EventGlIssue(source_type='documents',source_id=document.id,code=code))
            if errors:
                report.matched=False
            # Keep missing source mappings visible even if their role cannot be determined.
            if document.id in physical_only_ids or focus_account_id in plan or (errors and require_mapping) or any(
                line.account_id==focus_account_id for entry in matching for line in entry.lines
            ):
                reports.append(report)
    return reports,balance
