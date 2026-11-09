"""Real JWT -> FastAPI -> PostgreSQL -> protected reader E2E.

Uses a disposable clone of the actual ERP database.
Uses real authentication SQL and a dedicated PostgreSQL login.
No production route is registered.

The clone must contain an active user with company-scoped
employees.manage permission. No authorization is fabricated.
"""

import os
import time

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.database import get_db
from app.services.payroll_receipt_authenticated_channel import (
    read_authenticated_receipt_lifecycle,
)

pytestmark = pytest.mark.asyncio


def clone_url():
    raw = os.environ.get("ERP_R12_LIVE_DATABASE_URL", "")

    if "erp_r12_live_" not in raw:
        pytest.fail("Disposable clone URL required")

    return raw


async def fixture_identity():
    engine = create_async_engine(clone_url())

    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(text("""
                    SELECT
                        u.id AS actor_id,
                        ucr.company_id
                    FROM public.users u
                    JOIN public.user_company_roles ucr
                      ON ucr.user_id = u.id
                    JOIN public.role_permissions rp
                      ON rp.role_id = ucr.role_id
                    JOIN public.permissions p
                      ON p.id = rp.permission_id
                    WHERE u.is_active IS TRUE
                      AND ucr.is_active IS TRUE
                      AND p.name = 'employees.manage'
                    ORDER BY u.id, ucr.company_id
                    LIMIT 1
                """))
            ).mappings().first()

            if row is None:
                pytest.fail(
                    "No existing authorized actor in clone; "
                    "real positive authorization not proven"
                )

            other_company = await conn.scalar(text("""
                SELECT id FROM public.companies
                WHERE id <> :company
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.user_company_roles ucr
                      JOIN public.role_permissions rp
                        ON rp.role_id = ucr.role_id
                      JOIN public.permissions p
                        ON p.id = rp.permission_id
                      WHERE ucr.user_id = :actor
                        AND ucr.company_id = companies.id
                        AND ucr.is_active IS TRUE
                        AND p.name = 'employees.manage'
                  )
                ORDER BY id
                LIMIT 1
            """), {
                "actor": row["actor_id"],
                "company": row["company_id"],
            })

            return (
                row["actor_id"],
                row["company_id"],
                other_company,
            )
    finally:
        await engine.dispose()


def token(actor_id, *, expires=3600, token_type="access"):
    return jwt.encode(
        {
            "sub": str(actor_id),
            "type": token_type,
            "exp": int(time.time()) + expires,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def make_client():
    engine = create_async_engine(
        clone_url(),
        pool_pre_ping=True,
    )

    sessions = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    app = FastAPI()

    @app.get("/test/companies/{company_id}/receipts")
    async def endpoint(
        result=Depends(read_authenticated_receipt_lifecycle),
    ):
        return result

    async def clone_db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = clone_db

    return app, engine


def get(client, company, bearer=None, suffix=""):
    headers = {}

    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    return client.get(
        f"/test/companies/{company}/receipts{suffix}",
        headers=headers,
    )


async def test_real_postgresql_jwt_authorization():
    actor, company, other = await fixture_identity()
    app, engine = make_client()

    try:
        with TestClient(
            app,
            raise_server_exceptions=False,
        ) as client:
            # Actual JWT, actual SQL membership and actual
            # dedicated PostgreSQL login.
            positive = get(
                client,
                company,
                token(actor),
            )

            assert positive.status_code == 200, (
                positive.status_code,
                positive.text[:300],
            )

            payload = positive.json()
            assert payload["company_id"] == company
            assert payload["official_generation"] == "BLOCKED"
            assert payload["issuer_identity"] == "NOT_VERIFIED"
            assert payload["actual_withholding"] == "NOT_VERIFIED"

            # The HTTP client must not be able to choose actor_id.
            spoofed = get(
                client,
                company,
                token(actor),
                "?actor_id=999&authenticated_actor_id=999",
            )
            assert spoofed.status_code == 200

            # Actual JWT expiration.
            expired = get(
                client,
                company,
                token(actor, expires=-3600),
            )
            assert expired.status_code == 401

            # Reject refresh token as access token.
            refresh = get(
                client,
                company,
                token(actor, token_type="refresh"),
            )
            assert refresh.status_code == 401

            # Missing authentication.
            missing = get(client, company)
            assert missing.status_code == 401

            # Actual company permission denial when
            # another company exists.
            if other is None:
                pytest.fail(
                    "No unauthorized second company in clone; "
                    "cross-company E2E cannot be claimed"
                )

            denied = get(
                client,
                other,
                token(actor),
                "?actor_id=999",
            )
            assert denied.status_code == 403

    finally:
        await engine.dispose()


async def test_direct_application_role_still_denied():
    engine = create_async_engine(clone_url())

    try:
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text("SET LOCAL ROLE erp_user")
                )

                allowed = await conn.scalar(text("""
                    SELECT has_function_privilege(
                        current_user,
                        'public.p13_receipt_tenant_review(
                            integer,integer,integer
                        )',
                        'EXECUTE'
                    )
                """))

                assert allowed is False
    finally:
        await engine.dispose()
