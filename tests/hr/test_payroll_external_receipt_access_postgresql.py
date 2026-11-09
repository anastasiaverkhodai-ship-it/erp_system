"""Actual PostgreSQL erp_user receipt-access boundaries."""

import os

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio

REGISTRY = "public.payroll_external_receipt_registry"
VIEW = "public.payroll_external_receipt_lifecycle"


def arguments():
    database = os.environ.get("ERP_R12G_DATABASE", "")
    socket = os.environ.get("ERP_TEST_ADMIN_SOCKET", "")

    if (
        not database.startswith("erp_r12g1_")
        or not socket
        or not os.path.isdir(socket)
    ):
        pytest.fail("Disposable database required")

    return {
        "user": "anastasia",
        "host": socket,
        "database": database,
    }


async def test_returning_id_column_remains_readable():
    conn = await asyncpg.connect(**arguments())
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE erp_user")

            await conn.fetch(
                f"SELECT id FROM {REGISTRY} LIMIT 1"
            )

            role = await conn.fetchval("SELECT current_user")
            assert role == "erp_user"
    finally:
        await conn.close()


@pytest.mark.parametrize(
    "statement",
    [
        f"SELECT company_id FROM {REGISTRY} LIMIT 1",
        f"SELECT evidence_id FROM {REGISTRY} LIMIT 1",
        f"SELECT receipt_id FROM {REGISTRY} LIMIT 1",
        f"SELECT * FROM {REGISTRY} LIMIT 1",
        f"SELECT * FROM {VIEW} LIMIT 1",
        f"UPDATE {REGISTRY} SET amount=99 WHERE false",
        f"DELETE FROM {REGISTRY} WHERE false",
    ],
)
async def test_sensitive_receipt_operation_is_denied(statement):
    conn = await asyncpg.connect(**arguments())

    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE erp_user")

            with pytest.raises(
                asyncpg.InsufficientPrivilegeError
            ):
                async with conn.transaction():
                    await conn.execute(statement)

            assert await conn.fetchval(
                "SELECT current_user"
            ) == "erp_user"
    finally:
        await conn.close()


async def test_registry_insert_and_id_grants_are_preserved():
    conn = await asyncpg.connect(**arguments())
    try:
        privileges = await conn.fetchrow("""
            SELECT
                has_table_privilege(
                    'erp_user', $1, 'INSERT'
                ) AS can_insert,
                has_column_privilege(
                    'erp_user', $1, 'id', 'SELECT'
                ) AS can_return_id,
                has_table_privilege(
                    'erp_user', $1, 'SELECT'
                ) AS can_read_all,
                has_column_privilege(
                    'erp_user', $1, 'company_id', 'SELECT'
                ) AS can_read_company
        """, REGISTRY)

        assert privileges["can_insert"] is True
        assert privileges["can_return_id"] is True
        assert privileges["can_read_all"] is False
        assert privileges["can_read_company"] is False
    finally:
        await conn.close()
