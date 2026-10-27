
"""Real PostgreSQL write/idempotency/identity tests for D5 evidence."""
import importlib.util
from pathlib import Path
import pytest_asyncio
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

import hashlib
import json

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_deduction_execution_postgresql import seed

from app.models.position import Position
from app.models.employment_event import EmploymentEvent
from app.models.payroll_d5_professional_evidence import (
    PayrollD5ProfessionalEvidence,
)
from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_verified_mapping import (
    D5ProfessionalEvidence,
)
from app.services.payroll_d5_position_provenance import (
    D5PositionEvidence,
)
from app.services.payroll_d5_professional_history import (
    D5ProfessionalHistoryRecord,
)
import app.services.payroll_d5_trusted_classifier as trusted_registry
from app.services.payroll_d5_trusted_classifier import (
    ApprovedClassifierSource,
)

from app.services.payroll_d5_professional_write import (
    D5ProfessionalWriteError,
    create_verified_d5_professional_evidence,
)
from app.services.payroll_d5_position_provenance import (
    D5PositionProvenanceError,
)

pytestmark = pytest.mark.asyncio



@pytest_asyncio.fixture
async def d5_immutable_engine(employee_engine):
    """Install the actual c048 upgrade in a disposable test schema."""

    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "13d4a7b8c048_d5_professional_immutability.py"
    )

    assert migration_path.is_file()

    async with employee_engine.begin() as conn:
        current_schema = await conn.scalar(
            text("SELECT current_schema()")
        )

        assert (
            isinstance(current_schema, str)
            and current_schema.startswith("test_employee_")
        ), (
            "STOP: Refusing to execute D5 trigger migration "
            "outside disposable employee test schema"
        )

        def apply_existing_migration(sync_conn):
            context = MigrationContext.configure(sync_conn)
            operations = Operations(context)

            spec = importlib.util.spec_from_file_location(
                "test_d5_c048_migration",
                migration_path,
            )
            assert spec is not None and spec.loader is not None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            assert module.revision == "13d4a7b8c048"
            assert module.down_revision == "13d4a7b8c047"

            # Bind the unchanged migration to the disposable schema.
            # Execute its real upgrade(), not duplicated trigger SQL.
            module.op = operations
            module.upgrade()

        await conn.run_sync(apply_existing_migration)

        trigger_count = await conn.scalar(text("""
            SELECT count(*)
            FROM pg_trigger
            WHERE tgrelid =
                'payroll_d5_professional_evidence'::regclass
              AND tgname = 'trg_d5_professional_evidence_immutable'
              AND tgenabled = 'O'
              AND NOT tgisinternal
        """))

        assert trigger_count == 1, (
            "STOP: c048 did not install active trigger"
        )

    yield employee_engine


async def test_d5_verified_write_idempotency_and_negative_e2e(
    d5_immutable_engine, monkeypatch,
):
    employee_engine = d5_immutable_engine
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, actor, contract, calc, bank, beneficiary, claim = (
            await seed(db)
        )
        token = uuid4().hex
        day = contract.start_date + timedelta(days=2)

        position = Position(
            company_id=company.id,
            code="D5-F2-" + token[:12],
            name="Synthetic test position",
            is_active=True,
            created_by=actor.id,
        )
        db.add(position)
        await db.flush()

        event = EmploymentEvent(
            company_id=company.id,
            employment_contract_id=contract.id,
            event_type="hire",
            effective_date=day,
            order_number="F2-" + token,
            order_date=day,
            reason="D5 evidence E2E",
            request_key="F2-EVENT-" + token,
            before_state={},
            after_state={"position_id": position.id},
            request_payload={},
            created_by=actor.id,
        )
        db.add(event)
        await db.flush()

        source = D5EventSource(
            company_id=company.id,
            employment_event_id=event.id,
            employment_contract_id=contract.id,
            event_type="hire",
            effective_date=day,
            order_number=event.order_number,
            order_date=day,
            termination_basis=None,
        )

        professional = D5ProfessionalEvidence(
            employee_id=contract.employee_id,
            tax_number="TEST-TAX",
            professional_name="Synthetic test profession",
            profession_code="2419.2",
            position_name="Synthetic test position",
            employment_category="primary",
            evidence_reference="SYNTHETIC-TEST-" + token,
        )
        evidence = D5PositionEvidence(
            company_id=company.id,
            employee_id=contract.employee_id,
            employment_contract_id=contract.id,
            position_id=position.id,
            event_id=event.id,
            professional=professional,
        )
        history = D5ProfessionalHistoryRecord(
            company_id=company.id,
            employee_id=contract.employee_id,
            employment_contract_id=contract.id,
            position_id=position.id,
            effective_from=day,
            effective_to=None,
            documented_on=day,
            evidence_reference=professional.evidence_reference,
            professional_name=professional.professional_name,
            profession_code=professional.profession_code,
            position_name=professional.position_name,
            employment_category=professional.employment_category,
        )

        classifier_bytes = json.dumps({
            "source_reference": "synthetic-e2e-source",
            "source_version": "synthetic-e2e-v1",
            "entries": [{
                "profession_code": "2419.2",
                "professional_name": "Synthetic test profession",
                "effective_from": day.isoformat(),
                "effective_to": None,
            }, {
                "profession_code": "2419.3",
                "professional_name": "Synthetic test profession",
                "effective_from": day.isoformat(),
                "effective_to": None,
            }],
        }, sort_keys=True).encode("utf-8")

        approved_for_test = ApprovedClassifierSource(
            sha256=hashlib.sha256(classifier_bytes).hexdigest(),
            source_reference="synthetic-e2e-source",
            source_version="synthetic-e2e-v1",
        )

        # Trust anchor exists exclusively in this isolated test.
        assert trusted_registry.APPROVED_SOURCES == ()
        monkeypatch.setattr(
            trusted_registry,
            "APPROVED_SOURCES",
            (approved_for_test,),
        )

        args = dict(
            classifier_bytes=classifier_bytes,
            source=source,
            evidence=evidence,
            history=history,
            request_key="F2-WRITE-" + token,
            created_by=actor.id,
        )

        with pytest.raises(
            D5ProfessionalWriteError,
            match="Approved classifier source required",
        ):
            await create_verified_d5_professional_evidence(
                db,
                **{**args, "classifier_bytes": None},
            )

        with pytest.raises(
            D5ProfessionalWriteError,
            match="Classifier verification refused",
        ):
            await create_verified_d5_professional_evidence(
                db,
                **{
                    **args,
                    "classifier_bytes": classifier_bytes + b" ",
                },
            )

        with pytest.raises(
            D5ProfessionalWriteError,
            match="Classifier verification refused",
        ):
            await create_verified_d5_professional_evidence(
                db,
                **{
                    **args,
                    "history": replace(
                        history, profession_code="9999",
                    ),
                    "evidence": replace(
                        evidence,
                        professional=replace(
                            professional, profession_code="9999",
                        ),
                    ),
                },
            )

        first = await create_verified_d5_professional_evidence(
            db, **args
        )
        first_id = first.id
        assert first_id is not None

        # PostgreSQL trigger must exist and be active.
        trigger_count = await db.scalar(text("""
            SELECT count(*)
            FROM pg_trigger
            WHERE tgrelid =
                'payroll_d5_professional_evidence'::regclass
              AND tgname = 'trg_d5_professional_evidence_immutable'
              AND tgenabled = 'O'
              AND NOT tgisinternal
        """))
        assert trigger_count == 1, "Immutable trigger absent or disabled"

        original = (
            first.professional_name,
            first.profession_code,
            first.evidence_reference,
        )

        # Each rejected mutation runs in its own PostgreSQL savepoint.
        # Roll back only the failed savepoint, preserving the inserted row.
        for operation, statement in (
            (
                "UPDATE",
                text("""
                    UPDATE payroll_d5_professional_evidence
                    SET professional_name = 'FORGED VALUE'
                    WHERE id = :row_id AND company_id = :company_id
                """),
            ),
            (
                "DELETE",
                text("""
                    DELETE FROM payroll_d5_professional_evidence
                    WHERE id = :row_id AND company_id = :company_id
                """),
            ),
        ):
            savepoint = await db.begin_nested()
            try:
                await db.execute(
                    statement,
                    {"row_id": first_id, "company_id": company.id},
                )
            except Exception as exc:
                await savepoint.rollback()
                assert "D5 professional evidence is immutable" in str(exc), (
                    f"{operation} rejected for unexpected reason: {exc}"
                )
            else:
                await savepoint.rollback()
                raise AssertionError(
                    f"Direct SQL {operation} unexpectedly succeeded"
                )

            await db.refresh(first)
            assert (
                first.professional_name,
                first.profession_code,
                first.evidence_reference,
            ) == original

        # Failed UPDATE/DELETE must not remove or duplicate the row.
        surviving = await db.scalar(
            select(PayrollD5ProfessionalEvidence).where(
                PayrollD5ProfessionalEvidence.id == first_id,
                PayrollD5ProfessionalEvidence.company_id == company.id,
            )
        )
        assert surviving is not None
        assert surviving.id == first_id

        repeated = await create_verified_d5_professional_evidence(
            db, **args
        )
        assert repeated.id == first_id

        count = await db.scalar(
            select(func.count()).select_from(
                PayrollD5ProfessionalEvidence
            ).where(
                PayrollD5ProfessionalEvidence.company_id
                == company.id,
                PayrollD5ProfessionalEvidence.employment_event_id
                == event.id,
            )
        )
        assert count == 1

        with pytest.raises(
            D5ProfessionalWriteError,
            match="Conflicting",
        ):
            await create_verified_d5_professional_evidence(
                db,
                **{
                    **args,
                    "history": replace(
                        history,
                        profession_code="2419.3",
                    ),
                    "evidence": replace(
                        evidence,
                        professional=replace(
                            professional,
                            profession_code="2419.3",
                        ),
                    ),
                },
            )

        with pytest.raises(
            D5ProfessionalWriteError,
            match="correction workflow",
        ):
            await create_verified_d5_professional_evidence(
                db,
                **{
                    **args,
                    "request_key": "F2-SECOND-" + token,
                },
            )

        with pytest.raises(D5PositionProvenanceError):
            await create_verified_d5_professional_evidence(
                db,
                **{
                    **args,
                    "evidence": replace(
                        evidence, position_id=position.id + 999999,
                    ),
                    "history": replace(
                        history, position_id=position.id + 999999,
                    ),
                },
            )

        with pytest.raises(D5ProfessionalWriteError):
            await create_verified_d5_professional_evidence(
                db,
                **{
                    **args,
                    "history": replace(
                        history, documented_on=day + timedelta(days=1),
                    ),
                },
            )

        await db.rollback()
