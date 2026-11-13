"""Real PostgreSQL source-history binding and write E2E."""

from datetime import date, timedelta
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select
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
from app.services.payroll_d5_profession_history import (
    ProfessionEvent,
    build_candidate_history,
)
from app.services.payroll_d5_source_history_binding import (
    D5SourceHistoryBindingError,
)
from app.services.payroll_d5_hr_event_intake import (
    persist_verified_d5_hr_event,
)
import app.services.payroll_d5_trusted_classifier as trusted
from app.services.payroll_d5_trusted_classifier import (
    ApprovedClassifierSource,
)



from dataclasses import replace

from app.services.payroll_d5_hr_event_intake import (
    D5HREventIntakeError,
)

pytestmark = pytest.mark.asyncio


async def test_real_pg_transfer_intake_and_negative_paths(
    employee_engine, monkeypatch,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract, calc,
            bank, beneficiary, claim,
        ) = await seed(db)

        token = uuid4().hex
        # D5 supports events from 2026; the shared payroll seed starts earlier.
        day = max(date(2026, 1, 2), contract.start_date + timedelta(days=2))
        code = "2419.2"
        title = "Synthetic source-bound profession"

        position = Position(
            company_id=company.id,
            code="D5-BIND-" + token[:12],
            name="Synthetic source-bound position",
            is_active=True,
            created_by=actor.id,
        )
        db.add(position)
        await db.flush()

        event = EmploymentEvent(
            company_id=company.id,
            employment_contract_id=contract.id,
            event_type="transfer",
            effective_date=day,
            order_number="BIND-" + token,
            order_date=day,
            reason="D5 source-bound E2E",
            request_key="D5-BIND-EVENT-" + token,
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
            event_type="transfer",
            effective_date=day,
            order_number=event.order_number,
            order_date=day,
            termination_basis=None,
        )

        professional = D5ProfessionalEvidence(
            employee_id=contract.employee_id,
            tax_number="TEST-TAX",
            professional_name=title,
            profession_code=code,
            position_name=position.name,
            employment_category="primary",
            evidence_reference="BIND-EVIDENCE-" + token,
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
            professional_name=title,
            profession_code=code,
            position_name=position.name,
            employment_category="primary",
        )

        raw = json.dumps({
            "source_reference": "test-only-source-binding",
            "source_version": "TEST",
            "entries": [{
                "profession_code": code,
                "professional_name": title,
                "effective_from": day.isoformat(),
                "effective_to": None,
            }],
        }, sort_keys=True).encode("utf-8")

        assert all(item.source_version != "TEST" for item in trusted.APPROVED_SOURCES)

        monkeypatch.setattr(
            trusted,
            "APPROVED_SOURCES",
            (ApprovedClassifierSource(
                sha256=hashlib.sha256(raw).hexdigest(),
                source_reference="test-only-source-binding",
                source_version="TEST",
            ),),
        )

        def candidate(title_value=title, when=day):
            return build_candidate_history([
                ProfessionEvent(
                    profession_code=code,
                    operation="add",
                    effective_date=when,
                    candidate_dates=(when,),
                    title=title_value,
                    source_id="synthetic-test-order",
                    source_sha256="a" * 64,
                    source_reference="Synthetic test only",
                )
            ])

        args = dict(
            source=source,
            evidence=evidence,
            history=history,
            classifier_bytes=raw,
            request_key="D5-BOUND-WRITE-" + token,
            created_by=actor.id,
        )
        # A forged HR order must be rejected before writing.
        with pytest.raises(
            D5HREventIntakeError,
            match="differs from persisted source",
        ):
            await persist_verified_d5_hr_event(
                db,
                **{
                    **args,
                    "source": replace(
                        source, order_number="FORGED"
                    ),
                    "candidate_history": candidate(),
                },
            )

        # A nonexistent event cannot be used as a D5 source.
        with pytest.raises(
            D5HREventIntakeError,
            match="Persisted HR event not found",
        ):
            await persist_verified_d5_hr_event(
                db,
                **{
                    **args,
                    "source": replace(
                        source, employment_event_id=999999999
                    ),
                    "candidate_history": candidate(),
                },
            )


        with pytest.raises(
            D5SourceHistoryBindingError,
            match="title mismatch",
        ):
            await persist_verified_d5_hr_event(
                db,
                candidate_history=candidate("Forged title"),
                **args,
            )

        with pytest.raises(
            D5SourceHistoryBindingError,
            match="intervals differ",
        ):
            await persist_verified_d5_hr_event(
                db,
                candidate_history=candidate(
                    when=day - timedelta(days=1)
                ),
                **args,
            )

        count_before = await db.scalar(
            select(func.count()).select_from(
                PayrollD5ProfessionalEvidence
            ).where(
                PayrollD5ProfessionalEvidence.company_id
                == company.id,
                PayrollD5ProfessionalEvidence.employment_event_id
                == event.id,
            )
        )
        assert count_before == 0

        first = await persist_verified_d5_hr_event(
            db,
            candidate_history=candidate(),
            **args,
        )
        assert first.id is not None

        repeated = await persist_verified_d5_hr_event(
            db,
            candidate_history=candidate(),
            **args,
        )
        assert repeated.id == first.id

        count_after = await db.scalar(
            select(func.count()).select_from(
                PayrollD5ProfessionalEvidence
            ).where(
                PayrollD5ProfessionalEvidence.company_id
                == company.id,
                PayrollD5ProfessionalEvidence.employment_event_id
                == event.id,
            )
        )
        assert count_after == 1

        await db.rollback()
