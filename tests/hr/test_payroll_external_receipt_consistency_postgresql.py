"""Receipt/evidence consistency on disposable real PostgreSQL."""
import os
import asyncpg
from datetime import date
from decimal import Decimal
import pytest
pytestmark = pytest.mark.asyncio

def config():
    name = os.environ.get('ERP_R12E_DATABASE')
    socket = os.environ.get('ERP_TEST_ADMIN_SOCKET')
    if not name or not name.startswith('erp_r12e_checks_') or (not socket) or (not os.path.isdir(socket)):
        pytest.fail('Disposable PG database required')
    return {'user': 'anastasia', 'database': name, 'host': socket}
INSERT = "\nINSERT INTO public.payroll_external_receipt_registry (\n    company_id,\n    payroll_disbursement_id,\n    evidence_id,\n    issuer_id,\n    key_id,\n    receipt_id,\n    component,\n    amount,\n    payment_date,\n    source_date,\n    signed_payload_sha256,\n    signature_sha256\n) VALUES (\n    $1,$2,$3,'test-issuer','key-1',$4,$5,$6,\n    $7,DATE '2026-09-15',\n    repeat('a',64),repeat('b',64)\n)\n"

async def insert(conn, *, company=1, payment=10, evidence=100, receipt='receipt', component='personal_income_tax', amount='18.00', payment_date='2026-09-15'):
    return await conn.execute(INSERT, company, payment, evidence, receipt, component, Decimal(amount), date.fromisoformat(payment_date))

async def test_valid_receipt_matches_evidence():
    conn = await asyncpg.connect(**config())
    tx = conn.transaction()
    try:
        await tx.start()
        assert await insert(conn, receipt='valid') == 'INSERT 0 1'
        count = await conn.fetchval("\n            SELECT COUNT(*)\n            FROM public.payroll_external_receipt_registry\n            WHERE receipt_id='valid'\n        ")
        assert count == 1
    finally:
        await tx.rollback()
        await conn.close()

@pytest.mark.parametrize('case', ['amount', 'component', 'payment_date', 'wrong_payment', 'foreign_payment', 'foreign_company', 'unconfirmed', 'cancelled', 'reversed', 'stale_correction', 'revoked_candidate'])
async def test_invalid_receipt_is_rejected(case):
    conn = await asyncpg.connect(**config())
    tx = conn.transaction()
    try:
        await tx.start()
        values = {'receipt': 'negative-' + case}
        if case == 'amount':
            values['amount'] = '19.00'
        elif case == 'component':
            values['component'] = 'military_levy'
        elif case == 'payment_date':
            values['payment_date'] = '2026-09-16'
        elif case == 'wrong_payment':
            values['payment'] = 11
        elif case == 'foreign_payment':
            values['payment'] = 20
        elif case == 'foreign_company':
            values['company'] = 2
            values['payment'] = 20
        elif case == 'unconfirmed':
            await conn.execute('\n                UPDATE public.payroll_disbursements\n                SET confirmed_at=NULL\n                WHERE id=10\n            ')
        elif case == 'cancelled':
            await conn.execute('\n                UPDATE public.payroll_disbursements\n                SET cancelled_at=now()\n                WHERE id=10\n            ')
        elif case == 'reversed':
            await conn.execute("\n                UPDATE public.payroll_disbursements\n                SET reversed_on=DATE '2026-09-16'\n                WHERE id=10\n            ")
        elif case == 'stale_correction':
            await conn.execute('\n                INSERT INTO\n                  public.payroll_payment_tax_allocation_corrections\n                  (id, company_id, original_candidate_id)\n                VALUES (41,1,7)\n            ')
        elif case == 'revoked_candidate':
            await conn.execute("\n                INSERT INTO\n                  public.payroll_payment_tax_allocation_candidate_events\n                  (company_id,candidate_id,event_kind)\n                VALUES (1,7,'revoked')\n            ")
        with pytest.raises((asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError)):
            async with conn.transaction():
                await insert(conn, **values)
        count = await conn.fetchval('\n            SELECT COUNT(*)\n            FROM public.payroll_external_receipt_registry\n            WHERE receipt_id=$1\n        ', values['receipt'])
        assert count == 0
    finally:
        await tx.rollback()
        await conn.close()
