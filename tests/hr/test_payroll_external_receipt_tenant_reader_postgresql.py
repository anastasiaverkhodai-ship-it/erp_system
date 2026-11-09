"""Real PostgreSQL tenant and denial scenarios."""

import os

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.services.payroll_external_receipt_tenant_reader import (
    ReceiptTenantAccessDenied,
    ReceiptTenantReaderUnavailable,
    read_receipt_lifecycle,
)

pytestmark = pytest.mark.asyncio


def settings():
    db = os.environ.get("ERP_R12G2C_DATABASE", "")

    if not db.startswith("erp_r12g2c1_"):
        pytest.fail("Disposable PostgreSQL required")

    return db


async def connect():
    db = settings()

    engine = create_async_engine(
        f"postgresql+asyncpg://anastasia@localhost:5432/{db}",
        pool_pre_ping=True,
    )
    return engine


@pytest.mark.parametrize(
    "actor,company,count",
    [
        (1,1,2),
        (2,2,1),
    ],
)
async def test_authorized_tenant_only(actor, company, count):
    engine = await connect()

    try:
        async with engine.connect() as conn:
            output = await read_receipt_lifecycle(
                conn,
                company_id=company,
                authenticated_actor_id=actor,
            )

        assert len(output["records"]) == count
        assert all(
            row["company_id"] == company
            for row in output["records"]
        )
        assert output["official_generation"] == "BLOCKED"
        assert output["issuer_identity"] == "NOT_VERIFIED"
        assert output["actual_withholding"] == "NOT_VERIFIED"
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "actor,company",
    [
        (1,2),
        (2,1),
        (3,1),
        (4,1),
        (4,2),
        (999,1),
    ],
)
async def test_unauthorized_actor_denied(actor, company):
    engine = await connect()

    try:
        async with engine.connect() as conn:
            with pytest.raises(ReceiptTenantAccessDenied):
                await read_receipt_lifecycle(
                    conn,
                    company_id=company,
                    authenticated_actor_id=actor,
                )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "actor,company,limit",
    [
        (1,0,100),
        (0,1,100),
        (1,1,0),
        (1,1,501),
        (True,1,100),
        (1,True,100),
    ],
)
async def test_invalid_parameters_fail_closed(
    actor, company, limit
):
    engine = await connect()

    try:
        async with engine.connect() as conn:
            with pytest.raises(ValueError):
                await read_receipt_lifecycle(
                    conn,
                    company_id=company,
                    authenticated_actor_id=actor,
                    limit=limit,
                )
    finally:
        await engine.dispose()


async def test_application_role_cannot_read_lifecycle():
    db = settings()

    conn = await asyncpg.connect(
        user="anastasia",
        database=db,
        host=os.environ["ERP_TEST_ADMIN_SOCKET"],
    )

    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE erp_user")

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


async def test_untrusted_executor_fails_closed():
    class BlockedExecutor:
        async def execute(self, *_args, **_kwargs):
            raise PermissionError("No approved DB connection")

    with pytest.raises(ReceiptTenantReaderUnavailable):
        await read_receipt_lifecycle(
            BlockedExecutor(),
            company_id=1,
            authenticated_actor_id=1,
        )
