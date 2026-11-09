"""Protected company-scoped receipt function tests."""

import json
import os

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio


def config():
    name = os.environ.get("ERP_R12G2C2_DB", "")
    socket = os.environ.get("ERP_TEST_ADMIN_SOCKET", "")

    if (
        not name.startswith("erp_r12g2c2_test_")
        or not socket
        or not os.path.isdir(socket)
    ):
        pytest.fail("Disposable PostgreSQL required")

    return {
        "user": "anastasia",
        "host": socket,
        "database": name,
    }


async def call(conn, actor, company, limit=100):
    raw = await conn.fetchval("""
        SELECT public.p13_receipt_tenant_review(
          $1, $2, $3
        )::text
    """, actor, company, limit)
    return json.loads(raw)


@pytest.mark.parametrize(
    "actor,company,expected",
    [(1,1,2),(2,2,1)],
)
async def test_authorized_tenant(actor, company, expected):
    conn = await asyncpg.connect(**config())
    try:
        async with conn.transaction():
            await conn.execute(
                "SET LOCAL ROLE erp_receipt_reader"
            )
            result = await call(conn, actor, company)

            assert len(result["records"]) == expected
            assert all(
                item["company_id"] == company
                for item in result["records"]
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
async def test_unauthorized_tenant(actor, company):
    conn = await asyncpg.connect(**config())
    try:
        async with conn.transaction():
            await conn.execute(
                "SET LOCAL ROLE erp_receipt_reader"
            )
            with pytest.raises(
                asyncpg.InsufficientPrivilegeError
            ):
                async with conn.transaction():
                    await call(conn, actor, company)
    finally:
        await conn.close()


@pytest.mark.parametrize(
    "actor,company,limit",
    [(0,1,100),(1,0,100),(1,1,501)],
)
async def test_invalid_arguments(actor, company, limit):
    conn = await asyncpg.connect(**config())
    try:
        async with conn.transaction():
            await conn.execute(
                "SET LOCAL ROLE erp_receipt_reader"
            )
            with pytest.raises(
                asyncpg.InvalidParameterValueError
            ):
                async with conn.transaction():
                    await call(conn, actor, company, limit)
    finally:
        await conn.close()


async def test_application_role_cannot_execute():
    conn = await asyncpg.connect(**config())
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE erp_user")
            with pytest.raises(
                asyncpg.InsufficientPrivilegeError
            ):
                async with conn.transaction():
                    await call(conn, 1, 1)
    finally:
        await conn.close()


async def test_reader_cannot_select_view():
    conn = await asyncpg.connect(**config())
    try:
        async with conn.transaction():
            await conn.execute(
                "SET LOCAL ROLE erp_receipt_reader"
            )
            with pytest.raises(
                asyncpg.InsufficientPrivilegeError
            ):
                async with conn.transaction():
                    await conn.fetch(
                        "SELECT * FROM public."
                        "payroll_external_receipt_lifecycle"
                    )
    finally:
        await conn.close()
