"""Fail-closed, review-only external receipt lifecycle reader.

This service requires the caller to pass an actor_id obtained from
server-validated authentication, never from client request data.

It deliberately does not:
- grant lifecycle SELECT to erp_user;
- expose an unguarded API route;
- approve issuer identity, withholding, or statutory generation.

The SQL executor must be a trusted server-side read connection.
"""

from sqlalchemy import text


class ReceiptTenantAccessDenied(PermissionError):
    """No active company-specific payroll permission."""


class ReceiptTenantReaderUnavailable(RuntimeError):
    """The server has no approved lifecycle read channel."""


QUERY = text("""
WITH access AS (
    SELECT EXISTS (
        SELECT 1
        FROM public.users AS u
        JOIN public.user_company_roles AS ucr
          ON ucr.user_id = u.id
        JOIN public.role_permissions AS rp
          ON rp.role_id = ucr.role_id
        JOIN public.permissions AS perm
          ON perm.id = rp.permission_id
        WHERE u.id = :actor_id
          AND u.is_active IS TRUE
          AND ucr.company_id = :company_id
          AND ucr.is_active IS TRUE
          AND perm.name = 'employees.manage'
    ) AS allowed
),
receipt_rows AS (
    SELECT to_jsonb(l) AS payload
    FROM public.payroll_external_receipt_lifecycle AS l
    WHERE l.company_id = :company_id
      AND (SELECT allowed FROM access)
    LIMIT :limit
)
SELECT
    (SELECT allowed FROM access) AS authorized,
    COALESCE(
        (SELECT jsonb_agg(payload) FROM receipt_rows),
        '[]'::jsonb
    ) AS records
""")


async def read_receipt_lifecycle(
    db,
    *,
    company_id,
    authenticated_actor_id,
    limit=100,
):
    """Return only authorized company records.

    authenticated_actor_id must be provided by the server's
    get_current_user dependency. A client-supplied actor_id
    is never acceptable.

    Do not attach this method to an API endpoint until the
    trusted reader connection is independently reviewed.
    """
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(authenticated_actor_id) is not int
        or authenticated_actor_id <= 0
        or type(limit) is not int
        or not 1 <= limit <= 500
    ):
        raise ValueError("Invalid lifecycle reader arguments")

    # Both authorization and filtering are performed within
    # the same database statement.
    try:
        result = await db.execute(
            QUERY,
            {
                "actor_id": authenticated_actor_id,
                "company_id": company_id,
                "limit": limit,
            },
        )
        row = result.mappings().one()
    except Exception as exc:
        # Never fall back to privileged/unscoped reads.
        raise ReceiptTenantReaderUnavailable(
            "Approved lifecycle read channel unavailable"
        ) from exc

    if row["authorized"] is not True:
        raise ReceiptTenantAccessDenied(
            "Company lifecycle access denied"
        )

    records = row["records"]

    if not isinstance(records, list):
        raise ReceiptTenantReaderUnavailable(
            "Invalid lifecycle result"
        )

    for record in records:
        if (
            not isinstance(record, dict)
            or record.get("company_id") != company_id
            or record.get("official_generation") != "BLOCKED"
            or record.get("issuer_identity") != "NOT_VERIFIED"
            or record.get("actual_withholding") != "NOT_VERIFIED"
        ):
            raise ReceiptTenantReaderUnavailable(
                "Lifecycle trust boundary failed"
            )

    return {
        "company_id": company_id,
        "records": records,
        "issuer_identity": "NOT_VERIFIED",
        "actual_withholding": "NOT_VERIFIED",
        "official_generation": "BLOCKED",
    }
