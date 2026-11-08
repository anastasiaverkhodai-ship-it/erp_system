"""Fail-closed PostgreSQL runtime role separation audit.

An administrative psql connection is not evidence that
the application itself uses a restricted runtime role.
"""

from sqlalchemy import text


class RuntimeRoleSecurityError(ValueError):
    pass


def evaluate_runtime_role(row):
    if not isinstance(row, dict):
        raise RuntimeRoleSecurityError(
            "Missing PostgreSQL security evidence"
        )

    requirements = {
        "role_is_superuser": False,
        "role_bypasses_rls": False,
        "role_is_owner": False,
        "role_inherits_owner": False,
        "role_member_of_owner": False,
        "can_select": True,
        "can_insert": True,
        "can_update": False,
        "can_delete": False,
        "can_truncate": False,
        "immutable_trigger_enabled": True,
    }

    failures = [
        key for key, expected in requirements.items()
        if row.get(key) is not expected
    ]

    if row.get("runtime_role") != "erp_user":
        failures.append("unexpected_runtime_identity")

    if row.get("table_owner") != "erp_snapshot_owner":
        failures.append("unexpected_table_owner")

    return {
        "role_security": (
            "RESTRICTED_ROLE_VERIFIED"
            if not failures else "BLOCKED"
        ),
        "failures": sorted(failures),
        "runtime_role": row.get("runtime_role"),
        "table_owner": row.get("table_owner"),
        "official_generation": "BLOCKED",
    }


SECURITY_SQL = """
WITH target AS (
    SELECT c.oid, c.relowner,
           owner.rolname AS owner_name
    FROM pg_class c
    JOIN pg_namespace n
      ON n.oid=c.relnamespace
    JOIN pg_roles owner
      ON owner.oid=c.relowner
    WHERE n.nspname='public'
      AND c.relname=
        'payroll_regulatory_correction_snapshots'
      AND c.relkind IN ('r','p')
),
runtime AS (
    SELECT r.oid, r.rolsuper, r.rolbypassrls
    FROM pg_roles r
    WHERE r.rolname=current_user
)
SELECT
    current_user AS runtime_role,
    t.owner_name AS table_owner,
    r.rolsuper AS role_is_superuser,
    r.rolbypassrls AS role_bypasses_rls,
    r.oid=t.relowner AS role_is_owner,
    pg_has_role(r.oid,t.relowner,'USAGE')
        AS role_inherits_owner,
    pg_has_role(r.oid,t.relowner,'MEMBER')
        AS role_member_of_owner,
    has_table_privilege(r.oid,t.oid,'SELECT')
        AS can_select,
    has_table_privilege(r.oid,t.oid,'INSERT')
        AS can_insert,
    has_table_privilege(r.oid,t.oid,'UPDATE')
        AS can_update,
    has_table_privilege(r.oid,t.oid,'DELETE')
        AS can_delete,
    has_table_privilege(r.oid,t.oid,'TRUNCATE')
        AS can_truncate,
    EXISTS (
        SELECT 1 FROM pg_trigger tg
        WHERE tg.tgrelid=t.oid
          AND tg.tgname='trg_prcs_immutable'
          AND tg.tgenabled='O'
          AND NOT tg.tgisinternal
          AND (tg.tgtype::int & 8) <> 0
          AND (tg.tgtype::int & 16) <> 0
    ) AS immutable_trigger_enabled
FROM target t
CROSS JOIN runtime r
"""


async def audit_actual_runtime_connection(db):
    """Use the actual SQLAlchemy session, without SET ROLE."""
    result = (
        await db.execute(text(SECURITY_SQL))
    ).mappings().one_or_none()

    return evaluate_runtime_role(
        dict(result) if result is not None else None
    )


async def require_restricted_runtime_connection(db):
    result = await audit_actual_runtime_connection(db)

    if result["role_security"] != "RESTRICTED_ROLE_VERIFIED":
        raise RuntimeRoleSecurityError(
            "Restricted PostgreSQL runtime role not verified"
        )

    return result
