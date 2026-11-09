"""JWT-bound, fail-closed payroll receipt review channel.

Not exposed through a public router. The actor identity is
injected by FastAPI's authenticated company permission dependency.

ERP_RECEIPT_READER_DATABASE_URL must be provisioned separately
for a dedicated, least-privileged PostgreSQL login role.

Never use the application role, database owner or superuser
for this channel. No approval of withholding or statutory reports.
"""

import os

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.api.permissions import require_company_permission
from app.models.user import User


class ReceiptReadChannelBlocked(PermissionError):
    """No approved and authenticated read channel."""


ROLE = "erp_receipt_reader"

ENV_NAME = "ERP_RECEIPT_READER_DATABASE_URL"

ROLE_CHECK = text("""
    SELECT
        current_user AS active_role,
        session_user AS login_role,
        r.rolcanlogin,
        r.rolsuper,
        r.rolbypassrls,
        r.rolcreaterole,
        r.rolcreatedb,
        pg_has_role(
            session_user,
            'erp_receipt_reader',
            'MEMBER'
        ) AS trusted_membership,
        pg_has_role(
            session_user,
            'erp_snapshot_owner',
            'MEMBER'
        ) AS snapshot_owner_membership,
        has_table_privilege(
            session_user,
            'public.payroll_external_receipt_lifecycle',
            'SELECT'
        ) AS direct_lifecycle_access,
        has_table_privilege(
            session_user,
            'public.payroll_payment_tax_evidence',
            'SELECT'
        ) AS direct_evidence_access
    FROM pg_catalog.pg_roles AS r
    WHERE r.rolname = session_user
""")

REVIEW_QUERY = text("""
    SELECT public.p13_receipt_tenant_review(
        :actor_id,
        :company_id,
        :limit
    ) AS result
""")


def _reader_url():
    raw = os.environ.get(ENV_NAME)

    if not raw:
        raise ReceiptReadChannelBlocked(
            "Dedicated receipt reader connection not configured"
        )

    try:
        url = make_url(raw)
    except (TypeError, ValueError) as exc:
        raise ReceiptReadChannelBlocked(
            "Invalid receipt reader configuration"
        ) from exc

    if (
        url.drivername != "postgresql+asyncpg"
        or not url.database
        or not url.username
        or url.username in {
            "erp_user",
            "erp_snapshot_owner",
            "erp_receipt_reader",
            "anastasia",
            "postgres",
        }
    ):
        raise ReceiptReadChannelBlocked(
            "Dedicated PostgreSQL login role required"
        )

    return url


def _validate_role(row, expected_login):
    if (
        row is None
        or row["active_role"] != expected_login
        or row["login_role"] != expected_login
        or row["rolcanlogin"] is not True
        or row["rolsuper"] is not False
        or row["rolbypassrls"] is not False
        or row["rolcreaterole"] is not False
        or row["rolcreatedb"] is not False
        or row["trusted_membership"] is not True
        or row["snapshot_owner_membership"] is not False
        or row["direct_lifecycle_access"] is not False
        or row["direct_evidence_access"] is not False
    ):
        raise ReceiptReadChannelBlocked(
            "Reader login role violates security boundary"
        )


def _validate_result(result, company_id):
    if (
        not isinstance(result, dict)
        or result.get("company_id") != company_id
        or result.get("official_generation") != "BLOCKED"
        or result.get("issuer_identity") != "NOT_VERIFIED"
        or result.get("actual_withholding") != "NOT_VERIFIED"
        or not isinstance(result.get("records"), list)
    ):
        raise ReceiptReadChannelBlocked(
            "Unexpected lifecycle response"
        )

    for record in result["records"]:
        if (
            not isinstance(record, dict)
            or record.get("company_id") != company_id
            or record.get("official_generation") != "BLOCKED"
            or record.get("issuer_identity") != "NOT_VERIFIED"
            or record.get("actual_withholding") != "NOT_VERIFIED"
        ):
            raise ReceiptReadChannelBlocked(
                "Lifecycle record crossed security boundary"
            )

    return result


async def read_authenticated_receipt_lifecycle(
    company_id: int,
    current_user: User = Depends(
        require_company_permission("employees.manage")
    ),
):
    """Internal FastAPI dependency, not a registered API route.

    company_id is a target selector, not proof of authorization.
    require_company_permission verifies the JWT user and company
    permission before this function runs.

    No actor_id or reader credentials are accepted from the client.
    """
    if type(company_id) is not int or company_id <= 0:
        raise ReceiptReadChannelBlocked(
            "Invalid target company"
        )

    actor_id = getattr(current_user, "id", None)
    is_active = getattr(current_user, "is_active", None)

    if (
        type(actor_id) is not int
        or actor_id <= 0
        or is_active is not True
    ):
        raise ReceiptReadChannelBlocked(
            "Active authenticated user required"
        )

    url = _reader_url()

    engine = create_async_engine(
        url,
        poolclass=NullPool,
        pool_pre_ping=True,
    )

    try:
        async with engine.connect() as connection:
            async with connection.begin():
                role_result = await connection.execute(
                    ROLE_CHECK
                )
                role = role_result.mappings().one_or_none()

                _validate_role(role, url.username)

                # The login role must have explicitly granted
                # membership in the NOLOGIN review role.
                # Never SET ROLE on the ordinary ERP connection.
                await connection.execute(
                    text("SET LOCAL ROLE erp_receipt_reader")
                )

                active = await connection.scalar(
                    text("SELECT current_user")
                )

                if active != ROLE:
                    raise ReceiptReadChannelBlocked(
                        "Protected role activation failed"
                    )

                raw = await connection.scalar(
                    REVIEW_QUERY,
                    {
                        "actor_id": actor_id,
                        "company_id": company_id,
                        "limit": 100,
                    },
                )

                return _validate_result(raw, company_id)

    except ReceiptReadChannelBlocked:
        raise
    except Exception as exc:
        raise ReceiptReadChannelBlocked(
            "Receipt review channel unavailable or denied"
        ) from exc
    finally:
        await engine.dispose()
