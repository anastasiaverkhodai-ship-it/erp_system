"""Atomic bank matching of existing payouts; never creates accounting journals.

Caller owns commit. Immutable batch/allocations retain original provenance after
unmatching. Reversal releases the whole batch, leaving the actual payouts intact.
"""
from decimal import Decimal
from sqlalchemy import select
from app.models.payroll_bank_batch import PayrollBankBatch, PayrollBankBatchAllocation
from app.models.bank_statement_reconciliation import BankStatementReconciliation as Event, BankStatementReconciliationActiveLink as Link
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_bank_batch_validation import PayrollBankBatchValidationError as BatchError, _positive_id


async def active_batch_for_payout(db, company_id, payout_id):
    return await db.scalar(select(Link.id).join(PayrollBankBatchAllocation,
        (PayrollBankBatchAllocation.company_id == Link.company_id) &
        (PayrollBankBatchAllocation.batch_id == Link.payroll_bank_batch_id)).where(
            Link.company_id == company_id,
            PayrollBankBatchAllocation.payroll_disbursement_id == payout_id).limit(1))


async def _result(db, batch):
    event = await db.scalar(select(Event).where(Event.company_id == batch.company_id,
        Event.payroll_bank_batch_id == batch.id, Event.reversal_of_id.is_(None)))
    reversal = await db.scalar(select(Event.id).where(Event.reversal_of_id == event.id))
    members = (await db.scalars(select(PayrollBankBatchAllocation).where(
        PayrollBankBatchAllocation.company_id == batch.company_id,
        PayrollBankBatchAllocation.batch_id == batch.id).order_by(PayrollBankBatchAllocation.payroll_disbursement_id))).all()
    return {"batch_id": batch.id, "reconciliation_id": event.id,
        "reversal_id": reversal, "status": "REVERSED" if reversal else "POSTED",
        "amount": format(batch.amount, ".2f"), "member_count": len(members),
        "payroll_disbursement_ids": [m.payroll_disbursement_id for m in members],
        "new_journals_created": 0, "official_generation": "BLOCKED"}


@serialized_payroll_mutation(BatchError)
async def post_payroll_bank_batch(db, *, company_id, employee_id,
        bank_statement_line_id, payroll_disbursement_ids, created_by, request_key):
    from app.services.payroll_bank_batch_validation import validate_payroll_bank_batch
    for value, label in ((company_id, "company"), (employee_id, "employee"),
                         (bank_statement_line_id, "statement line"), (created_by, "actor")):
        _positive_id(value, label)
    if (type(request_key) is not str or not request_key or len(request_key) > 100
            or not request_key.isascii() or any(c.isspace() for c in request_key)):
        raise BatchError("Invalid batch request key")
    if (type(payroll_disbursement_ids) not in (list, tuple) or len(payroll_disbursement_ids) < 2
            or any(type(i) is not int or i <= 0 for i in payroll_disbursement_ids)
            or len(set(payroll_disbursement_ids)) != len(payroll_disbursement_ids)):
        raise BatchError("Invalid or duplicate disbursement")
    async with db.begin_nested():
        existing = await db.scalar(select(PayrollBankBatch).where(
            PayrollBankBatch.company_id == company_id, PayrollBankBatch.request_key == request_key))
        if existing is not None:
            result = await _result(db, existing)
            if (existing.employee_id != employee_id or existing.bank_statement_line_id != bank_statement_line_id
                    or result["payroll_disbursement_ids"] != sorted(payroll_disbursement_ids)):
                raise BatchError("Idempotency key payload mismatch")
            return result
        candidate = await validate_payroll_bank_batch(db, company_id=company_id,
            employee_id=employee_id, bank_statement_line_id=bank_statement_line_id,
            payroll_disbursement_ids=payroll_disbursement_ids)
        batch = PayrollBankBatch(company_id=company_id, employee_id=employee_id,
            bank_statement_line_id=bank_statement_line_id, request_key=request_key,
            amount=Decimal(candidate["amount"]), currency_code="UAH", created_by=created_by)
        db.add(batch)
        await db.flush()
        for member in candidate["members"]:
            db.add(PayrollBankBatchAllocation(company_id=company_id, batch_id=batch.id,
                payroll_disbursement_id=member["payroll_disbursement_id"],
                journal_entry_id=member["journal_entry_id"], amount=Decimal(member["amount"])))
        await db.flush()
        event = Event(company_id=company_id, bank_statement_line_id=bank_statement_line_id,
            payroll_bank_batch_id=batch.id, matched_amount=batch.amount,
            currency_code="UAH", created_by=created_by)
        db.add(event)
        await db.flush()
        db.add(Link(company_id=company_id, bank_statement_line_id=bank_statement_line_id,
            payroll_bank_batch_id=batch.id, reconciliation_id=event.id))
        await db.flush()
        return await _result(db, batch)


@serialized_payroll_mutation(BatchError)
async def reverse_payroll_bank_batch(db, *, company_id, batch_id, reversed_by):
    from app.services.bank_statement_reconciliation_service import reverse_bank_statement_reconciliation
    _positive_id(batch_id, "batch")
    _positive_id(reversed_by, "actor")
    async with db.begin_nested():
        batch = await db.scalar(select(PayrollBankBatch).where(
            PayrollBankBatch.company_id == company_id, PayrollBankBatch.id == batch_id))
        if batch is None:
            raise BatchError("Bank batch unavailable")
        result = await _result(db, batch)
        if result["status"] == "REVERSED":
            return result
        await reverse_bank_statement_reconciliation(db, company_id=company_id,
            reconciliation_id=result["reconciliation_id"], reversed_by=reversed_by)
        return await _result(db, batch)
