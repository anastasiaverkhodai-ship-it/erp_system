"""Fail-closed role audit for protected payroll tax custody."""

from sqlalchemy import text


TABLES = (
    "payroll_payment_tax_secure_custody",
    "payroll_payment_tax_document_integrity",
    "payroll_payment_tax_document_reviews",
)


class CustodyRoleSecurityError(ValueError):
    pass


def evaluate_custody_role(rows):
    if not isinstance(rows, list):
        raise CustodyRoleSecurityError(
            "Missing custody role evidence"
        )

    by_name = {}

    for row in rows:
        if not isinstance(row, dict):
            raise CustodyRoleSecurityError(
                "Invalid custody security evidence"
            )

        name = row.get("table_name")

        if name not in TABLES or name in by_name:
            raise CustodyRoleSecurityError(
                "Unexpected or duplicate custody table"
            )

        by_name[name] = row

    failures = []

    if set(by_name) != set(TABLES):
        failures.append("INCOMPLETE_TABLE_COVERAGE")

    for table in TABLES:
        row = by_name.get(table)

        if row is None:
            continue

        requirements = {
            "runtime_role": "erp_user",
            "owner_role": "erp_snapshot_owner",
            "is_superuser": False,
            "bypass_rls": False,
            "is_owner": False,
            "inherits_owner": False,
            "can_assume_owner": False,
            "can_select": True,
            "can_insert": True,
            "can_update": False,
            "can_delete": False,
            "can_truncate": False,
            "immutable_trigger_enabled": True,
        }

        for field, expected in requirements.items():
            actual = row.get(field)

            if type(expected) is bool:
                valid = (
                    type(actual) is bool
                    and actual is expected
                )
            else:
                valid = (
                    type(actual) is type(expected)
                    and actual == expected
                )

            if not valid:
                failures.append(table + ":" + field)

    return {
        "security_status": (
            "RESTRICTED_CUSTODY_ROLE_VERIFIED"
            if not failures else "BLOCKED"
        ),
        "failures": sorted(set(failures)),
        "table_count": len(by_name),
        "runtime_identity": "erp_user",
        "official_generation": "BLOCKED",
    }


SQL = """
WITH names(name) AS (
    VALUES
    ('payroll_payment_tax_secure_custody'),
    ('payroll_payment_tax_document_integrity'),
    ('payroll_payment_tax_document_reviews')
)
SELECT
    names.name AS table_name,
    current_user AS runtime_role,
    owner.rolname AS owner_role,
    r.rolsuper AS is_superuser,
    r.rolbypassrls AS bypass_rls,
    r.oid = c.relowner AS is_owner,
    pg_has_role(
        r.oid, c.relowner, 'USAGE'
    ) AS inherits_owner,
    pg_has_role(
        r.oid, c.relowner, 'SET'
    ) AS can_assume_owner,
    has_table_privilege(
        r.oid, c.oid, 'SELECT'
    ) AS can_select,
    has_table_privilege(
        r.oid, c.oid, 'INSERT'
    ) AS can_insert,
    has_table_privilege(
        r.oid, c.oid, 'UPDATE'
    ) AS can_update,
    has_table_privilege(
        r.oid, c.oid, 'DELETE'
    ) AS can_delete,
    has_table_privilege(
        r.oid, c.oid, 'TRUNCATE'
    ) AS can_truncate,
    EXISTS (
        SELECT 1
        FROM pg_trigger tg
        WHERE tg.tgrelid=c.oid
          AND tg.tgname='trg_p13_custody_immutable'
          AND tg.tgenabled IN ('O','A')
          AND NOT tg.tgisinternal
          AND (tg.tgtype::int & 8) <> 0
          AND (tg.tgtype::int & 16) <> 0
    ) AS immutable_trigger_enabled
FROM names
JOIN pg_class c
  ON c.oid=to_regclass(format('public.%I',names.name))
JOIN pg_roles owner ON owner.oid=c.relowner
JOIN pg_roles r ON r.rolname=current_user
ORDER BY names.name
"""


async def audit_custody_runtime_connection(db):
    rows = (
        await db.execute(text(SQL))
    ).mappings().all()

    return evaluate_custody_role(
        [dict(row) for row in rows]
    )


async def require_custody_runtime_connection(db):
    result = await audit_custody_runtime_connection(db)

    if (
        result["security_status"]
        != "RESTRICTED_CUSTODY_ROLE_VERIFIED"
    ):
        raise CustodyRoleSecurityError(
            "Restricted custody runtime not verified"
        )

    return result
