"""Real PostgreSQL HR-source correction-chain acceptance."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_d5_database_field_projection_postgresql import (
    setup_case,
)

from app.models.employment_event import EmploymentEvent
from app.models.payroll_d5_professional_evidence import (
    PayrollD5ProfessionalEvidence,
)
from app.services.payroll_d5_database_correction_chain import (
    D5CorrectionChainError,
    review_persisted_d5_correction_chain,
)


pytestmark = pytest.mark.asyncio


async def setup_correction(db):
    company, actor, contract, employee, original, old = (
        await setup_case(db, "hire")
    )

    token = uuid4().hex

    reversal = EmploymentEvent(
        company_id=company.id,
        employment_contract_id=contract.id,
        event_type="reversal",
        effective_date=original.effective_date,
        order_number="CORR-REV-" + token,
        order_date=original.effective_date,
        reason="Reversal of initial source",
        request_key="CORR-REV-KEY-" + token,
        before_state={},
        after_state={},
        request_payload={},
        reversal_of_id=original.id,
        created_by=actor.id,
    )
    db.add(reversal)
    await db.flush()

    replacement = EmploymentEvent(
        company_id=company.id,
        employment_contract_id=contract.id,
        event_type="hire",
        effective_date=original.effective_date,
        order_number="CORR-NEW-" + token,
        order_date=original.effective_date,
        reason="Explicit HR source replacement",
        request_key="CORR-NEW-KEY-" + token,
        before_state={},
        after_state={
            "position_id": old.position_id,
        },
        request_payload={
            "replaces_employment_event_id": original.id,
        },
        created_by=actor.id,
    )
    db.add(replacement)
    await db.flush()

    new_evidence = PayrollD5ProfessionalEvidence(
        company_id=company.id,
        employee_id=employee.id,
        employment_contract_id=contract.id,
        employment_event_id=replacement.id,
        position_id=old.position_id,
        effective_from=original.effective_date,
        effective_to=None,
        documented_on=original.effective_date,
        professional_name=old.professional_name,
        profession_code=old.profession_code,
        position_name=old.position_name,
        employment_category=old.employment_category,
        evidence_reference="CORR-EVIDENCE-" + token,
        request_key="CORR-EVIDENCE-KEY-" + token,
        created_by=actor.id,
    )
    db.add(new_evidence)
    await db.flush()

    return (
        company, actor, contract, original,
        reversal, replacement, new_evidence,
    )


async def test_real_pg_correction_chain_and_scope(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract, original,
            reversal, replacement, new_evidence,
        ) = await setup_correction(db)

        kwargs = dict(
            company_id=company.id,
            original_event_id=original.id,
            reversal_event_id=reversal.id,
            replacement_event_id=replacement.id,
        )

        review = await review_persisted_d5_correction_chain(
            db, **kwargs
        )

        assert review["original_event_status"] == "REVERSED"
        assert review["replacement_event_status"] == (
            "ACTIVE_CANDIDATE"
        )
        assert review["replacement_candidate"][
            "fields"
        ]["13"] == new_evidence.profession_code
        assert review["official_generation"] == "BLOCKED"
        assert review["official_rows_generated"] == 0
        assert len(review["correction_chain_sha256"]) == 64

        repeated = await review_persisted_d5_correction_chain(
            db, **kwargs
        )
        assert repeated["correction_chain_sha256"] == (
            review["correction_chain_sha256"]
        )

        with pytest.raises(
            D5CorrectionChainError,
            match="absent from company scope",
        ):
            await review_persisted_d5_correction_chain(
                db,
                **{
                    **kwargs,
                    "company_id": company.id + 100000000,
                },
            )

        await db.rollback()


async def test_real_pg_replacement_tamper_and_reversal(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract, original,
            reversal, replacement, new_evidence,
        ) = await setup_correction(db)

        kwargs = dict(
            company_id=company.id,
            original_event_id=original.id,
            reversal_event_id=reversal.id,
            replacement_event_id=replacement.id,
        )

        replacement.request_payload = {}
        await db.flush()

        with pytest.raises(
            D5CorrectionChainError,
            match="Explicit persisted",
        ):
            await review_persisted_d5_correction_chain(
                db, **kwargs
            )

        replacement.request_payload = {
            "replaces_employment_event_id": original.id,
        }
        await db.flush()

        token = uuid4().hex
        db.add(EmploymentEvent(
            company_id=company.id,
            employment_contract_id=contract.id,
            event_type="reversal",
            effective_date=replacement.effective_date,
            order_number="CORR-REV2-" + token,
            order_date=replacement.effective_date,
            reason="Reversal of replacement",
            request_key="CORR-REV2-KEY-" + token,
            before_state={},
            after_state={},
            request_payload={},
            reversal_of_id=replacement.id,
            created_by=actor.id,
        ))
        await db.flush()

        with pytest.raises(
            D5CorrectionChainError,
            match="Replacement event was reversed",
        ):
            await review_persisted_d5_correction_chain(
                db, **kwargs
            )

        await db.rollback()


async def test_real_pg_duplicate_replacement_is_blocked(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract, original,
            reversal, replacement, new_evidence,
        ) = await setup_correction(db)

        # Confirm that the original chain works before
        # another competing replacement is inserted.
        kwargs = {
            "company_id": company.id,
            "original_event_id": original.id,
            "reversal_event_id": reversal.id,
            "replacement_event_id": replacement.id,
        }

        before = await review_persisted_d5_correction_chain(
            db, **kwargs
        )
        assert before["official_generation"] == "BLOCKED"

        token = uuid4().hex

        competitor = EmploymentEvent(
            company_id=company.id,
            employment_contract_id=contract.id,
            event_type=original.event_type,
            effective_date=original.effective_date,
            order_number="DUPLICATE-CORR-" + token,
            order_date=original.effective_date,
            reason="Competing replacement source",
            request_key="DUPLICATE-CORR-KEY-" + token,
            before_state={},
            after_state={
                "position_id": new_evidence.position_id,
            },
            request_payload={
                "replaces_employment_event_id": original.id,
            },
            created_by=actor.id,
        )

        db.add(competitor)
        await db.flush()

        with pytest.raises(
            D5CorrectionChainError,
            match="Unique active replacement link",
        ):
            await review_persisted_d5_correction_chain(
                db, **kwargs
            )

        await db.rollback()
