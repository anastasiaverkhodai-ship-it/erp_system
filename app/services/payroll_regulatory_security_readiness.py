"""Fail-closed regulatory readiness and PostgreSQL snapshot security.

This audit never authorizes official statutory form generation.
It identifies database privilege and regulatory evidence blockers.
"""

from sqlalchemy import text

SNAPSHOT_TABLE = "public.payroll_regulatory_correction_snapshots"
REPORTS = ("d1", "4df", "d5", "d6")


def evaluate_security_evidence(row):
    if not isinstance(row, dict):
        return {
            "security_status": "BLOCKED",
            "failures": ["MISSING_DATABASE_EVIDENCE"],
            "official_generation": "BLOCKED",
        }

    failures = []

    if row.get("snapshot_table_exists") is not True:
        failures.append("SNAPSHOT_TABLE_MISSING")

    if row.get("trigger_enabled") is not True:
        failures.append("IMMUTABILITY_TRIGGER_NOT_ENABLED")

    if row.get("role_is_superuser") is not False:
        failures.append("PRIVILEGED_RUNTIME_ROLE")

    if row.get("role_can_inherit_owner") is not False:
        failures.append("RUNTIME_CAN_ASSUME_TABLE_OWNER")

    if row.get("role_is_table_owner") is not False:
        failures.append("RUNTIME_OWNS_IMMUTABLE_TABLE")

    if row.get("can_update") is not False:
        failures.append("RUNTIME_HAS_UPDATE_PRIVILEGE")

    if row.get("can_delete") is not False:
        failures.append("RUNTIME_HAS_DELETE_PRIVILEGE")

    if row.get("can_truncate") is not False:
        failures.append("RUNTIME_HAS_TRUNCATE_PRIVILEGE")

    if row.get("can_insert") is not True:
        failures.append("RUNTIME_CANNOT_INSERT_SNAPSHOT")

    return {
        "security_status": (
            "ROLE_SEPARATION_VERIFIED"
            if not failures else "BLOCKED"
        ),
        "failures": sorted(set(failures)),
        "runtime_role": row.get("runtime_role"),
        "table_owner": row.get("table_owner"),
        "official_generation": "BLOCKED",
    }


def regulatory_readiness(*, security, source_status=None):
    """Separate internal financial evidence from official authorization."""

    source_status = source_status or {}

    if not isinstance(security, dict):
        security = {"security_status": "BLOCKED"}

    reports = {}

    for report in REPORTS:
        blockers = []

        if security.get("security_status") != "ROLE_SEPARATION_VERIFIED":
            blockers.append("POSTGRESQL_ROLE_SEPARATION_NOT_VERIFIED")

        if report in ("d1", "4df"):
            blockers.append("OFFICIAL_FORM_FIELD_MAPPING_NOT_VERIFIED")
        else:
            blockers.append("OFFICIAL_NORMATIVE_SOURCE_COVERAGE_NOT_VERIFIED")

        if source_status.get(report) != "APPROVED":
            blockers.append("REPORT_SOURCE_APPROVAL_NOT_VERIFIED")

        reports[report] = {
            "official_generation": "BLOCKED",
            "regulatory_status": "NOT_VERIFIED",
            "blockers": sorted(set(blockers)),
        }

    return {
        "security": security,
        "reports": reports,
        "all_reports_officially_ready": False,
        "official_generation": "BLOCKED",
    }


async def review_postgresql_snapshot_security(db):
    """Audit privileges of the ACTUAL DB connection used by this call."""

    row = (
        await db.execute(
            text("""
                WITH target AS (
                    SELECT
                        c.oid AS relid,
                        c.relowner AS owner_oid,
                        r.rolname AS owner_name
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN pg_roles r ON r.oid = c.relowner
                    WHERE n.nspname = 'public'
                      AND c.relname =
                        'payroll_regulatory_correction_snapshots'
                      AND c.relkind IN ('r', 'p')
                ),
                runtime AS (
                    SELECT
                        r.oid AS role_oid,
                        r.rolname,
                        r.rolsuper
                    FROM pg_roles r
                    WHERE r.rolname = current_user
                )
                SELECT
                    current_user AS runtime_role,
                    t.owner_name AS table_owner,
                    (t.relid IS NOT NULL) AS snapshot_table_exists,
                    COALESCE(
                        (
                            SELECT bool_or(
                                tg.tgname = 'trg_prcs_immutable'
                                AND tg.tgenabled = 'O'
                                AND (tg.tgtype::int & 8) <> 0
                                AND (tg.tgtype::int & 16) <> 0
                            )
                            FROM pg_trigger tg
                            WHERE tg.tgrelid = t.relid
                              AND NOT tg.tgisinternal
                        ), false
                    ) AS trigger_enabled,
                    runtime.rolsuper AS role_is_superuser,
                    (runtime.role_oid = t.owner_oid)
                        AS role_is_table_owner,
                    CASE
                        WHEN t.owner_oid IS NULL THEN NULL
                        ELSE pg_has_role(
                            runtime.role_oid,
                            t.owner_oid,
                            'USAGE'
                        )
                    END AS role_can_inherit_owner,
                    CASE
                        WHEN t.relid IS NULL THEN NULL
                        ELSE has_table_privilege(
                            runtime.role_oid,
                            t.relid,
                            'INSERT'
                        )
                    END AS can_insert,
                    CASE
                        WHEN t.relid IS NULL THEN NULL
                        ELSE has_table_privilege(
                            runtime.role_oid,
                            t.relid,
                            'UPDATE'
                        )
                    END AS can_update,
                    CASE
                        WHEN t.relid IS NULL THEN NULL
                        ELSE has_table_privilege(
                            runtime.role_oid,
                            t.relid,
                            'DELETE'
                        )
                    END AS can_delete,
                    CASE
                        WHEN t.relid IS NULL THEN NULL
                        ELSE has_table_privilege(
                            runtime.role_oid,
                            t.relid,
                            'TRUNCATE'
                        )
                    END AS can_truncate
                FROM runtime
                LEFT JOIN target t ON true
            """)
        )
    ).mappings().one()

    evidence = dict(row)
    result = evaluate_security_evidence(evidence)
    result["database_evidence"] = evidence
    return result
