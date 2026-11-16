"""Real financial bank batch acceptance, exclusively on a disposable full copy."""
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.models.payroll_bank_batch import PayrollBankBatch as Batch, PayrollBankBatchAllocation as Allocation
from app.models.bank_statement_reconciliation import BankStatementReconciliation as Event, BankStatementReconciliationActiveLink as Link
from app.services.payroll_bank_batch_service import post_payroll_bank_batch, reverse_payroll_bank_batch, BatchError
from app.services.payroll_bank_reconciliation_service import reconcile_payroll_disbursement
from app.services.bank_statement_reconciliation_service import BankStatementReconciliationError
from app.services.accounting_reversal import reverse_journal_entry, AccountingReversalError

spec = importlib.util.spec_from_file_location("batch_real_sources", Path(__file__).with_name("test_phase13_cross_calculation_real_postgresql.py"))
real = importlib.util.module_from_spec(spec)
spec.loader.exec_module(real)
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def case():
    engine = create_async_engine(real._postgres_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        base = real._fixture_module()
        company, user, employee, bank, calculations = await real._create_real_sources(db, base)
        result = await real.post_cross_calculation_payments(db, company_id=company.id,
            employee_id=employee.id, bank_account_id=bank.id, cash_desk_id=None,
            payment_date=date(2026, 11, 10), created_by=user.id, request_key="batch-fixture",
            items=[{"payroll_calculation_id": c.id, "amount": str(1000 * (i+1))} for i,c in enumerate(calculations)])
        statement = base.BankStatement(**base._required_values(base.BankStatement, {
            "company_id": company.id, "bank_account_id": bank.id, "external_id": "batch-e2e", "created_by": user.id}))
        db.add(statement)
        await db.flush()
        async def line(amount="-3000.00", key="batch-e2e"):
            obj = base.BankStatementLine(**base._required_values(base.BankStatementLine, {
                "company_id": company.id, "bank_account_id": bank.id,
                "bank_statement_id": statement.id, "external_line_id": key,
                "amount": Decimal(amount), "currency_code": "UAH"}))
            db.add(obj)
            await db.flush()
            return obj
        bank_line = await line()
        args = dict(company_id=company.id, employee_id=employee.id, bank_statement_line_id=bank_line.id,
            payroll_disbursement_ids=[x["payroll_disbursement_id"] for x in result["components"]],
            created_by=user.id, request_key="batch-request")
        c = SimpleNamespace(engine=engine, Session=Session, db=db, args=args, line=line,
            bank_line=bank_line, company_id=company.id, user_id=user.id, result=result)
        try:
            yield c
        finally:
            await db.rollback()
    await engine.dispose()


async def counts(c):
    return tuple([await c.db.scalar(select(func.count(m.id)).where(m.company_id == c.company_id))
                  for m in (Batch, Allocation, Event, Link)])


async def reverse(c, batch_id):
    return await reverse_payroll_bank_batch(c.db, company_id=c.company_id, batch_id=batch_id, reversed_by=c.user_id)


async def test_persistence_replay_reversal_and_rematch(case):
    c = case
    before = await real._counts(c.db, c.company_id)
    first = await post_payroll_bank_batch(c.db, **c.args)
    assert await counts(c) == (1, 2, 1, 1)
    assert first["status"] == "POSTED" and first["amount"] == "3000.00"
    assert first["official_generation"] == "BLOCKED"
    # Commit then read with an independent session: persistent, not session-local state.
    await c.db.commit()
    async with c.Session() as other:
        assert await post_payroll_bank_batch(other, **c.args) == first
        await other.commit()
    assert await post_payroll_bank_batch(c.db, **{**c.args, "payroll_disbursement_ids": list(reversed(c.args["payroll_disbursement_ids"]))}) == first
    undone = await reverse(c, first["batch_id"])
    assert undone["status"] == "REVERSED" and undone["reversal_id"]
    assert await reverse(c, first["batch_id"]) == undone
    assert await post_payroll_bank_batch(c.db, **c.args) == undone
    assert await counts(c) == (1, 2, 2, 0)
    fresh = await post_payroll_bank_batch(c.db, **{**c.args, "request_key": "rematch"})
    assert fresh["batch_id"] != first["batch_id"]
    assert await counts(c) == (2, 4, 3, 1)
    assert await real._counts(c.db, c.company_id) == before


@pytest.mark.parametrize("invalid", ["duplicate", "amount", "employee", "company", "currency", "incoming", "cancelled", "unposted"])
async def test_invalid_sources_leave_no_batch(case, invalid):
    c = case
    args = dict(c.args)
    if invalid == "duplicate":
        args["payroll_disbursement_ids"] = [args["payroll_disbursement_ids"][0]] * 2
    elif invalid == "amount":
        c.bank_line.amount = Decimal("-3001.00")
    elif invalid == "employee":
        args["employee_id"] += 1000000
    elif invalid == "company":
        args["company_id"] += 1000000
    elif invalid == "currency":
        c.bank_line.currency_code = "USD"
    elif invalid == "incoming":
        c.bank_line.amount = Decimal("3000.00")
    elif invalid == "cancelled":
        # Use the persisted reversal state as the rejected source.
        payout = await c.db.get(real.PayrollDisbursement, args["payroll_disbursement_ids"][0])
        payout.reversed_on = date(2026, 11, 11)
    else:
        await reverse_journal_entry(c.db, c.company_id, c.result["components"][0]["journal_entry_id"], date(2026, 11, 11), c.user_id)
    await c.db.flush()
    before = await real._counts(c.db, c.company_id)
    with pytest.raises(BatchError):
        await post_payroll_bank_batch(c.db, **args)
    assert await counts(c) == (0, 0, 0, 0)
    assert await real._counts(c.db, c.company_id) == before


async def test_idempotency_conflict_and_member_reuse(case):
    c = case
    await post_payroll_bank_batch(c.db, **c.args)
    second = await c.line(key="second")
    for changed in ({"bank_statement_line_id": second.id}, {"employee_id": c.args["employee_id"] + 1}):
        with pytest.raises(BatchError, match="Idempotency"):
            await post_payroll_bank_batch(c.db, **{**c.args, **changed})
    with pytest.raises(BatchError, match="already reconciled"):
        await post_payroll_bank_batch(c.db, **{**c.args, "request_key": "reuse", "bank_statement_line_id": second.id})
    with pytest.raises(BankStatementReconciliationError, match="already reconciled"):
        await reconcile_payroll_disbursement(c.db, company_id=c.company_id,
            payroll_disbursement_id=c.args["payroll_disbursement_ids"][0], bank_statement_line_id=second.id,
            matched_amount=Decimal("1000.00"), currency_code="UAH", created_by=c.user_id)
    assert await counts(c) == (1, 2, 1, 1)


async def test_single_match_blocks_batch(case):
    c = case
    second = await c.line("-1000.00", key="single")
    await reconcile_payroll_disbursement(c.db, company_id=c.company_id,
        payroll_disbursement_id=c.args["payroll_disbursement_ids"][0], bank_statement_line_id=second.id,
        matched_amount=Decimal("1000.00"), currency_code="UAH", created_by=c.user_id)
    with pytest.raises(BatchError, match="already reconciled"):
        await post_payroll_bank_batch(c.db, **c.args)
    assert await counts(c) == (0, 0, 1, 1)


@pytest.mark.parametrize("reversing", [False, True])
async def test_late_failure_is_atomic(case, reversing):
    c = case
    if reversing:
        first = await post_payroll_bank_batch(c.db, **c.args)
    before = await counts(c)
    def fail(conn, cursor, statement, parameters, context, executemany):
        prefix = "DELETE FROM" if reversing else "INSERT INTO"
        if statement.startswith(prefix + " bank_statement_reconciliation_active_links"):
            raise RuntimeError("injected final write failure")
    event.listen(c.engine.sync_engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="final write"):
            if reversing:
                await reverse(c, first["batch_id"])
            else:
                await post_payroll_bank_batch(c.db, **c.args)
    finally:
        event.remove(c.engine.sync_engine, "before_cursor_execute", fail)
    assert await counts(c) == before
    if reversing:
        assert (await post_payroll_bank_batch(c.db, **c.args))["status"] == "POSTED"
    else:
        assert (await post_payroll_bank_batch(c.db, **c.args))["status"] == "POSTED"


async def test_immutable_allocations_and_batch(case):
    from sqlalchemy.exc import DBAPIError
    c = case
    await post_payroll_bank_batch(c.db, **c.args)
    for sql in ("UPDATE payroll_bank_batches SET amount=1 WHERE company_id=:c",
                "DELETE FROM payroll_bank_batch_allocations WHERE company_id=:c",
                "UPDATE bank_statement_reconciliations SET matched_amount=1 WHERE company_id=:c"):
        with pytest.raises(DBAPIError):
            async with c.db.begin_nested():
                await c.db.execute(text(sql), {"c": c.company_id})
    assert await counts(c) == (1, 2, 1, 1)


async def test_concurrent_exact_replay(case):
    import asyncio
    c = case
    await c.db.commit()
    async def post():
        async with c.Session() as db:
            async with db.begin():
                return await post_payroll_bank_batch(db, **c.args)
    a, b = await asyncio.wait_for(asyncio.gather(post(), post()), 20)
    assert a == b
    assert await counts(c) == (1, 2, 1, 1)


async def test_payout_reversal_requires_batch_unmatch(case):
    c = case
    first = await post_payroll_bank_batch(c.db, **c.args)
    with pytest.raises(AccountingReversalError, match="Unmatch"):
        async with c.db.begin_nested():
            await reverse_journal_entry(c.db, c.company_id,
                c.result["components"][0]["journal_entry_id"], date(2026, 11, 11), c.user_id)
    assert await counts(c) == (1, 2, 1, 1)
    await reverse(c, first["batch_id"])
    await reverse_journal_entry(c.db, c.company_id,
        c.result["components"][0]["journal_entry_id"], date(2026, 11, 11), c.user_id)


async def test_competing_requests_share_no_members(case):
    import asyncio
    c = case
    second = await c.line(key="competing")
    second_id = second.id
    await c.db.commit()
    async def post(args):
        async with c.Session() as db:
            async with db.begin():
                try:
                    return await post_payroll_bank_batch(db, **args)
                except BatchError:
                    return None
    results = await asyncio.wait_for(asyncio.gather(post(c.args), post({**c.args,
        "request_key": "competing", "bank_statement_line_id": second_id})), 20)
    assert sum(r is not None for r in results) == 1
    assert await counts(c) == (1, 2, 1, 1)
