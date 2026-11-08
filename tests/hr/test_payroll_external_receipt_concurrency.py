"""R12-D real concurrent replay checks on disposable PostgreSQL."""

import asyncio
import os

import asyncpg
import pytest


def db_config():
    name = os.getenv("ERP_R12D_RACE_DATABASE")
    socket = os.getenv("ERP_TEST_ADMIN_SOCKET")

    if (
        not name
        or not name.startswith("erp_r12d_race_")
        or not socket
        or not os.path.isdir(socket)
    ):
        pytest.fail("Disposable PostgreSQL required")

    return {
        "database": name,
        "user": "anastasia",
        "host": socket,
    }


async def connect():
    return await asyncpg.connect(**db_config())


INSERT = """
    INSERT INTO public.payroll_external_receipt_registry (
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
    ) VALUES (
        $1, $2, $3,
        'test-issuer',
        'test-key',
        'same-receipt',
        'personal_income_tax',
        18.00,
        DATE '2026-09-15',
        DATE '2026-09-15',
        repeat('a',64),
        repeat('b',64)
    )
"""


@pytest.mark.asyncio
async def test_concurrent_replay_exactly_one_commit():
    first = await connect()
    second = await connect()

    tx = None
    task = None

    try:
        tx = first.transaction()
        await tx.start()
        await first.execute(INSERT, 1, 10, 100)

        async def competing():
            try:
                await second.execute(
                    INSERT, 2, 20, 200
                )
                return "WRONG_SECOND_SUCCESS"
            except asyncpg.UniqueViolationError:
                return "REPLAY_BLOCKED"

        task = asyncio.create_task(competing())

        # The duplicate insert must wait for the first
        # transaction before PostgreSQL resolves uniqueness.
        await asyncio.sleep(0.2)

        assert not task.done()

        await tx.commit()
        tx = None

        result = await asyncio.wait_for(task, 15)
        assert result == "REPLAY_BLOCKED"

        count = await first.fetchval("""
            SELECT count(*)
            FROM public.payroll_external_receipt_registry
            WHERE issuer_id='test-issuer'
              AND receipt_id='same-receipt'
        """)

        assert count == 1

        company = await first.fetchval("""
            SELECT company_id
            FROM public.payroll_external_receipt_registry
            WHERE issuer_id='test-issuer'
              AND receipt_id='same-receipt'
        """)

        assert company == 1

    finally:
        if tx is not None:
            await tx.rollback()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(
                task, return_exceptions=True
            )
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_application_role_cannot_change_receipt():
    conn = await connect()

    try:
        await conn.execute("SET ROLE erp_user")

        with pytest.raises(
            asyncpg.InsufficientPrivilegeError
        ):
            await conn.execute("""
                UPDATE public.payroll_external_receipt_registry
                SET amount=99.00
            """)

        with pytest.raises(
            asyncpg.InsufficientPrivilegeError
        ):
            await conn.execute("""
                DELETE FROM
                  public.payroll_external_receipt_registry
            """)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_cross_company_payment_denied():
    conn = await connect()

    try:
        # New evidence avoids the UNIQUE conflict from
        # the preceding concurrent replay test.
        await conn.execute(
            "INSERT INTO public.payroll_payment_tax_evidence (id, company_id) VALUES (101, 1)"
        )
        with pytest.raises(
            asyncpg.ForeignKeyViolationError
        ) as exc:
            await conn.execute("""
                INSERT INTO
                  public.payroll_external_receipt_registry (
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
                ) VALUES (
                    1,20,101,
                    'test-issuer',
                    'test-key',
                    'invalid-company',
                    'personal_income_tax',
                    18.00,
                    DATE '2026-09-15',
                    DATE '2026-09-15',
                    repeat('a',64),
                    repeat('b',64)
                )
            """)
        assert exc.value.constraint_name == (
            "fk_external_receipt_payment"
        )
    finally:
        await conn.close()
