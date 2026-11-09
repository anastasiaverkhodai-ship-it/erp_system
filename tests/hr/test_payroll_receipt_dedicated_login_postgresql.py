"""Real network login and protected receipt role boundaries."""

import json
import os

import asyncpg
import pytest
from sqlalchemy.engine import URL

from app.services.payroll_receipt_authenticated_channel import (
    ROLE_CHECK,
    REVIEW_QUERY,
    _validate_result,
    _validate_role,
)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio


def params():
    db = os.environ.get("ERP_RECEIPT_TEST_DB", "")
    login = os.environ.get("ERP_RECEIPT_TEST_LOGIN", "")
    password = os.environ.get("ERP_RECEIPT_TEST_PASSWORD", "")
    port = os.environ.get("ERP_RECEIPT_TEST_PORT", "")

    if (
        not db.startswith("erp_receipt_live_")
        or not login.startswith("erp_receipt_probe_")
        or not password
        or not port.isdigit()
    ):
        pytest.fail("Disposable login configuration required")

    return db, login, password, int(port)


async def connect():
    db, login, password, port = params()
    return await asyncpg.connect(
        user=login,
        password=password,
        database=db,
        host="127.0.0.1",
        port=port,
    )


@pytest.mark.parametrize(
    "actor,company,expected",
    [(1,1,2),(2,2,1)],
)
async def test_real_login_authorized_company(
    actor, company, expected
):
    conn = await connect()

    try:
        async with conn.transaction():
            await conn.execute(
                "SET LOCAL ROLE erp_receipt_reader"
            )

            raw = await conn.fetchval("""
                SELECT public.p13_receipt_tenant_review(
                    $1,$2,100
                )::text
            """, actor, company)

            result = json.loads(raw)

            assert len(result["records"]) == expected
            assert all(
                row["company_id"] == company
                for row in result["records"]
            )
            assert result["official_generation"] == "BLOCKED"
            assert result["issuer_identity"] == "NOT_VERIFIED"
            assert result["actual_withholding"] == "NOT_VERIFIED"
    finally:
        await conn.close()


@pytest.mark.parametrize(
    "actor,company",
    [(1,2),(2,1),(3,1),(4,1),(4,2),(999,1)],
)
async def test_real_login_denies_unauthorized_actor(
    actor, company
):
    conn = await connect()

    try:
        async with conn.transaction():
            await conn.execute(
                "SET LOCAL ROLE erp_receipt_reader"
            )

            with pytest.raises(
                asyncpg.InsufficientPrivilegeError
            ):
                async with conn.transaction():
                    await conn.fetchval("""
                        SELECT public.p13_receipt_tenant_review(
                            $1,$2,100
                        )
                    """, actor, company)
    finally:
        await conn.close()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM public.payroll_external_receipt_lifecycle",
        "SELECT * FROM public.payroll_payment_tax_evidence",
        "SELECT * FROM public.users",
    ],
)
async def test_login_direct_read_denied(sql):
    conn = await connect()

    try:
        with pytest.raises(
            asyncpg.InsufficientPrivilegeError
        ):
            await conn.fetch(sql)
    finally:
        await conn.close()


async def test_login_is_not_superuser_or_owner():
    conn = await connect()

    try:
        role = await conn.fetchrow("""
            SELECT
                r.rolcanlogin,
                r.rolsuper,
                r.rolbypassrls,
                r.rolcreaterole,
                r.rolcreatedb
            FROM pg_roles r
            WHERE r.rolname=current_user
        """)

        assert role["rolcanlogin"] is True
        assert role["rolsuper"] is False
        assert role["rolbypassrls"] is False
        assert role["rolcreaterole"] is False
        assert role["rolcreatedb"] is False
    finally:
        await conn.close()


async def test_actual_server_bridge_query():
    db, login, password, port = params()

    url = URL.create(
        "postgresql+asyncpg",
        username=login,
        password=password,
        host="127.0.0.1",
        port=port,
        database=db,
    )

    engine = create_async_engine(
        url,
        poolclass=NullPool,
        pool_pre_ping=True,
    )

    try:
        async with engine.connect() as conn:
            async with conn.begin():
                row = (
                    await conn.execute(ROLE_CHECK)
                ).mappings().one()

                _validate_role(row, login)

                await conn.exec_driver_sql(
                    "SET LOCAL ROLE erp_receipt_reader"
                )

                raw = await conn.scalar(
                    REVIEW_QUERY,
                    {
                        "actor_id": 1,
                        "company_id": 1,
                        "limit": 100,
                    },
                )

                result = _validate_result(raw, 1)

                assert len(result["records"]) == 2
                assert result["official_generation"] == "BLOCKED"
    finally:
        await engine.dispose()


async def test_no_direct_reader_privileges():
    conn = await connect()

    try:
        values = await conn.fetchrow("""
            SELECT
                has_table_privilege(
                    current_user,
                    'public.payroll_external_receipt_lifecycle',
                    'SELECT'
                ) AS view_read,
                has_table_privilege(
                    current_user,
                    'public.payroll_payment_tax_evidence',
                    'SELECT'
                ) AS evidence_read,
                pg_has_role(
                    current_user,
                    'erp_snapshot_owner',
                    'MEMBER'
                ) AS snapshot_member
        """)

        assert values["view_read"] is False
        assert values["evidence_read"] is False
        assert values["snapshot_member"] is False
    finally:
        await conn.close()
