"""Read-only role boundary inspection on isolated PostgreSQL."""

import json

import pytest
from sqlalchemy import text

from app.core.database import AsyncSessionLocal


@pytest.mark.asyncio
async def test_erp_runtime_role_boundary_inventory():
    async with AsyncSessionLocal() as db:
        assert db.bind.dialect.name == "postgresql"

        database = await db.scalar(
            text("SELECT current_database()")
        )
        assert database.startswith("erp_r3_copy_")

        identity = (await db.execute(text("""
            SELECT
                current_user AS runtime_role,
                session_user AS session_role,
                current_database() AS database_name,
                pg_get_userbyid(d.datdba) AS database_owner,
                r.rolsuper AS is_superuser,
                r.rolcreaterole AS can_create_roles,
                r.rolcreatedb AS can_create_databases,
                r.rolinherit AS inherits_roles
            FROM pg_database d
            JOIN pg_roles r
              ON r.rolname = current_user
            WHERE d.datname = current_database()
        """))).mappings().one()

        relations = (await db.execute(text("""
            SELECT
                n.nspname AS schema_name,
                c.relkind AS object_kind,
                pg_get_userbyid(c.relowner) AS owner_name,
                COUNT(*) AS object_count
            FROM pg_class c
            JOIN pg_namespace n
              ON n.oid = c.relnamespace
            WHERE n.nspname NOT IN (
                'pg_catalog', 'information_schema'
            )
              AND n.nspname NOT LIKE 'pg_toast%'
              AND c.relkind IN (
                  'r', 'p', 'v', 'm', 'S', 'f'
              )
            GROUP BY
                n.nspname, c.relkind, c.relowner
            ORDER BY
                n.nspname, c.relkind, owner_name
        """))).mappings().all()

        core_tables = (await db.execute(text("""
            SELECT
                c.relname AS table_name,
                pg_get_userbyid(c.relowner) AS owner_name,
                has_table_privilege(
                    current_user, c.oid, 'SELECT'
                ) AS can_select,
                has_table_privilege(
                    current_user, c.oid, 'INSERT'
                ) AS can_insert,
                has_table_privilege(
                    current_user, c.oid, 'UPDATE'
                ) AS can_update,
                has_table_privilege(
                    current_user, c.oid, 'DELETE'
                ) AS can_delete,
                has_table_privilege(
                    current_user, c.oid, 'TRIGGER'
                ) AS can_manage_triggers
            FROM pg_class c
            JOIN pg_namespace n
              ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname IN (
                  'journal_entries',
                  'journal_entry_lines',
                  'payments',
                  'payroll_disbursements',
                  'cash_desks',
                  'cash_desk_opening_evidence',
                  'cash_desk_opening_reviews',
                  'alembic_version'
              )
            ORDER BY c.relname
        """))).mappings().all()

        namespace = (await db.execute(text("""
            SELECT
                n.nspname,
                pg_get_userbyid(n.nspowner) AS owner_name,
                has_schema_privilege(
                    current_user, n.oid, 'CREATE'
                ) AS runtime_can_create
            FROM pg_namespace n
            WHERE n.nspname = 'public'
        """))).mappings().one()

        triggers = (await db.execute(text("""
            SELECT
                c.relname AS table_name,
                t.tgname AS trigger_name,
                t.tgenabled AS enabled_status
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE NOT t.tgisinternal
              AND n.nspname = 'public'
              AND c.relname IN (
                  'journal_entries', 'journal_entry_lines'
              )
            ORDER BY c.relname, t.tgname
        """))).mappings().all()

        print("ROLE_CONTRACT_START")
        print(json.dumps({
            "identity": dict(identity),
            "public_schema": dict(namespace),
            "owned_objects": [
                dict(x) for x in relations
            ],
            "critical_tables": [
                dict(x) for x in core_tables
            ],
            "security_triggers": [
                dict(x) for x in triggers
            ],
        }, ensure_ascii=False, default=str, sort_keys=True))
        print("ROLE_CONTRACT_END")

        assert any(
            row["table_name"] == "journal_entries"
            for row in core_tables
        )
        assert any(
            row["trigger_name"]
            == "trg_finalized_journal_header_immutability"
            for row in triggers
        )
        await db.rollback()
