"""Real PostgreSQL receipt lifecycle after source changes."""

import os
from datetime import date
from decimal import Decimal

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio


def connection_args():
    name = os.environ.get("ERP_R12F_DATABASE", "")
    socket = os.environ.get("ERP_TEST_ADMIN_SOCKET", "")

    if (
        not name.startswith("erp_r12f_checks_")
        or not socket
        or not os.path.isdir(socket)
    ):
        pytest.fail("Disposable PostgreSQL required")

    return {
        "user": "anastasia",
        "database": name,
        "host": socket,
    }


async def register(conn, receipt):
    return await conn.execute("""
        INSERT INTO public.payroll_external_receipt_registry (
            company_id, payroll_disbursement_id,
            evidence_id, issuer_id, key_id, receipt_id,
            component, amount, payment_date, source_date,
            signed_payload_sha256, signature_sha256
        ) VALUES (
            1, 10, 100, 'lifecycle-issuer', 'test-key',
            $1, 'personal_income_tax',
            $2, $3, $3,
            repeat('a',64), repeat('b',64)
        )
    """, receipt, Decimal("18.00"), date(2026, 9, 15))


async def lifecycle(conn, receipt):
    return await conn.fetchrow("""
        SELECT lifecycle_status, source_current,
               actual_withholding, issuer_identity,
               official_generation
        FROM public.payroll_external_receipt_lifecycle
        WHERE company_id=1 AND receipt_id=$1
    """, receipt)


@pytest.mark.parametrize(
    "case,expected",
    [
        ("current", "SOURCE_CURRENT"),
        ("unconfirmed", "PAYMENT_INACTIVE"),
        ("cancelled", "PAYMENT_INACTIVE"),
        ("reversed", "PAYMENT_INACTIVE"),
        ("currency", "PAYMENT_INACTIVE"),
        ("amount", "SOURCE_MISMATCH"),
        ("date", "SOURCE_MISMATCH"),
        ("correction", "CORRECTION_SUPERSEDED"),
        ("revoked", "SOURCE_REVOKED"),
    ],
)
async def test_lifecycle_changes_without_mutating_receipt(case, expected):
    conn = await asyncpg.connect(**connection_args())
    tx = conn.transaction()

    try:
        await tx.start()
        receipt = "lifecycle-" + case

        assert await register(conn, receipt) == "INSERT 0 1"

        initial = await lifecycle(conn, receipt)
        assert initial["lifecycle_status"] == "SOURCE_CURRENT"
        assert initial["source_current"] is True

        if case == "unconfirmed":
            await conn.execute("""
                UPDATE public.payroll_disbursements
                SET confirmed_at=NULL
                WHERE id=10
            """)

        elif case == "cancelled":
            await conn.execute("""
                UPDATE public.payroll_disbursements
                SET cancelled_at=now()
                WHERE id=10
            """)

        elif case == "reversed":
            await conn.execute("""
                UPDATE public.payroll_disbursements
                SET reversed_on=DATE '2026-09-16'
                WHERE id=10
            """)

        elif case == "currency":
            await conn.execute("""
                UPDATE public.payroll_disbursements
                SET currency_code='EUR'
                WHERE id=10
            """)

        elif case == "amount":
            await conn.execute("""
                UPDATE public.payroll_payment_tax_evidence
                SET amount=19
                WHERE id=100
            """)

        elif case == "date":
            await conn.execute("""
                UPDATE public.payroll_disbursements
                SET payment_date=DATE '2026-09-16'
                WHERE id=10
            """)

        elif case == "correction":
            await conn.execute("""
                INSERT INTO
                public.payroll_payment_tax_allocation_corrections
                (id, company_id, original_candidate_id)
                VALUES (41,1,7)
            """)

        elif case == "revoked":
            await conn.execute("""
                INSERT INTO
                public.payroll_payment_tax_allocation_candidate_events
                (company_id,candidate_id,event_kind)
                VALUES (1,7,'revoked')
            """)

        result = await lifecycle(conn, receipt)

        assert result["lifecycle_status"] == expected
        assert result["source_current"] == (
            expected == "SOURCE_CURRENT"
        )
        assert result["actual_withholding"] == "NOT_VERIFIED"
        assert result["issuer_identity"] == "NOT_VERIFIED"
        assert result["official_generation"] == "BLOCKED"

        # The historical receipt remains present.
        count = await conn.fetchval("""
            SELECT count(*)
            FROM public.payroll_external_receipt_registry
            WHERE company_id=1 AND receipt_id=$1
        """, receipt)

        assert count == 1

    finally:
        await tx.rollback()
        await conn.close()


async def test_lifecycle_company_filter():
    conn = await asyncpg.connect(**connection_args())
    tx = conn.transaction()

    try:
        await tx.start()
        await register(conn, "company-filter")

        own = await conn.fetchval("""
            SELECT count(*)
            FROM public.payroll_external_receipt_lifecycle
            WHERE company_id=1
              AND receipt_id='company-filter'
        """)

        other = await conn.fetchval("""
            SELECT count(*)
            FROM public.payroll_external_receipt_lifecycle
            WHERE company_id=2
              AND receipt_id='company-filter'
        """)

        assert own == 1
        assert other == 0

    finally:
        await tx.rollback()
        await conn.close()


async def test_historical_receipt_still_immutable():
    conn = await asyncpg.connect(**connection_args())
    tx = conn.transaction()

    try:
        await tx.start()
        await register(conn, "immutable-lifecycle")

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute("""
                    UPDATE public.payroll_external_receipt_registry
                    SET amount=99
                    WHERE receipt_id='immutable-lifecycle'
                """)

        row = await lifecycle(conn, "immutable-lifecycle")
        assert row["lifecycle_status"] == "SOURCE_CURRENT"
        assert row["official_generation"] == "BLOCKED"

    finally:
        await tx.rollback()
        await conn.close()
