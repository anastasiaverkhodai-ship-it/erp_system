"""Real application-role receipt and source-lock checks."""

import os
from datetime import date
from decimal import Decimal

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio


def config():
    database = os.environ.get("ERP_R12G2B_DATABASE", "")
    socket = os.environ.get("ERP_TEST_ADMIN_SOCKET", "")

    if not database.startswith("erp_r12g2b_checks_"):
        pytest.fail("Disposable database required")

    if not socket or not os.path.isdir(socket):
        pytest.fail("PostgreSQL socket missing")

    return {
        "database": database,
        "host": socket,
        "user": "anastasia",
    }


async def insert(conn, company, payment, evidence, amount, name):
    return await conn.fetchval("""
        INSERT INTO public.payroll_external_receipt_registry
        (
            company_id,
            payroll_disbursement_id,
            evidence_id,
            issuer_id,
            key_id,
            receipt_id,
            component,
            amount,
            payment_date,
            source_date,
            signed_payload_sha256,
            signature_sha256
        )
        VALUES (
            $1,$2,$3,
            'r12g2b-test-issuer',
            'r12g2b-test-key',
            $4,
            'personal_income_tax',
            $5,$6,$6,
            repeat('a',64),
            repeat('b',64)
        )
        RETURNING id
    """,
        company,
        payment,
        evidence,
        name,
        Decimal(str(amount)),
        date(2026, 9, 15),
    )


@pytest.mark.parametrize(
    "company,payment,evidence,amount,name",
    [
        (1,10,100,"18.00","valid-company-one"),
        (2,20,200,"21.00","valid-company-two"),
    ],
)
async def test_real_role_insert_returns_id(
    company, payment, evidence, amount, name
):
    conn = await asyncpg.connect(**config())

    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE erp_user")
            assert await conn.fetchval(
                "SELECT current_user"
            ) == "erp_user"

            result = await insert(
                conn, company, payment, evidence, amount, name
            )

            assert isinstance(result, int)
            assert result > 0
    finally:
        await conn.close()


@pytest.mark.parametrize(
    "company,payment,evidence,amount",
    [
        (1,20,200,"21.00"),
        (1,10,100,"19.00"),
        (1,30,300,"11.00"),
        (2,10,100,"18.00"),
    ],
)
async def test_invalid_source_is_rejected(
    company, payment, evidence, amount
):
    conn = await asyncpg.connect(**config())

    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE erp_user")

            with pytest.raises(
                (
                    asyncpg.CheckViolationError,
                    asyncpg.ForeignKeyViolationError,
                )
            ):
                async with conn.transaction():
                    await insert(
                        conn,
                        company,
                        payment,
                        evidence,
                        amount,
                        "invalid-" + str(company) + "-"
                        + str(payment) + "-" + str(amount),
                    )
    finally:
        await conn.close()


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM public.payroll_payment_tax_evidence",
        "SELECT * FROM public.payroll_external_receipt_lifecycle",
    ],
)
async def test_direct_financial_reads_are_denied(statement):
    conn = await asyncpg.connect(**config())

    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE erp_user")

            with pytest.raises(
                asyncpg.InsufficientPrivilegeError
            ):
                async with conn.transaction():
                    await conn.fetch(statement)
    finally:
        await conn.close()


async def test_protected_role_permissions_are_scoped():
    conn = await asyncpg.connect(**config())

    try:
        privileges = await conn.fetchrow("""
            SELECT
                has_table_privilege(
                    'erp_user',
                    'public.payroll_payment_tax_evidence',
                    'SELECT'
                ) AS app_can_read,
                has_column_privilege(
                    'erp_user',
                    'public.payroll_payment_tax_evidence',
                    'id',
                    'UPDATE'
                ) AS app_can_update,
                has_column_privilege(
                    'erp_snapshot_owner',
                    'public.payroll_payment_tax_evidence',
                    'id',
                    'UPDATE'
                ) AS owner_can_lock,
                (
                    SELECT prosecdef
                    FROM pg_proc
                    WHERE oid=to_regprocedure(
                        'public.p13_receipt_check_evidence()'
                    )
                ) AS security_definer
        """)

        assert privileges["app_can_read"] is False
        assert privileges["app_can_update"] is False
        assert privileges["owner_can_lock"] is True
        assert privileges["security_definer"] is True
    finally:
        await conn.close()
