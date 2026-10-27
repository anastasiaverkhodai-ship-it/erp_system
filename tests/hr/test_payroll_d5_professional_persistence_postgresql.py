
"""Check the real PostgreSQL migration and its constraints."""
import pytest
from sqlalchemy import inspect, text
from app.models.payroll_d5_professional_evidence import PayrollD5ProfessionalEvidence
from test_employee_foundation import employee_engine

pytestmark = pytest.mark.asyncio


async def test_d5_professional_table_exists_with_constraints(employee_engine):
    async with employee_engine.connect() as conn:
        def inspect_table(sync_conn):
            inspector = inspect(sync_conn)
            table = "payroll_d5_professional_evidence"
            assert inspector.has_table(table)
            cols = {x["name"] for x in inspector.get_columns(table)}
            assert {
                "company_id", "employee_id",
                "employment_contract_id", "employment_event_id",
                "position_id", "effective_from", "effective_to",
                "documented_on", "professional_name",
                "profession_code", "position_name",
                "employment_category", "evidence_reference",
                "request_key", "created_by", "created_at",
            }.issubset(cols)
            fk = inspector.get_foreign_keys(table)
            assert len(fk) >= 3
            assert {x["referred_table"] for x in fk} >= {
                "employment_contracts", "employment_events", "positions"
            }
            unique = inspector.get_unique_constraints(table)
            assert any(
                set(x["column_names"]) == {"company_id", "request_key"}
                for x in unique
            )
        await conn.run_sync(inspect_table)

        count = await conn.scalar(
            text("SELECT count(*) FROM payroll_d5_professional_evidence")
        )
        assert count >= 0
