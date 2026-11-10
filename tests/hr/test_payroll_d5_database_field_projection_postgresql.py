"""Real PostgreSQL acceptance for the DB-bound D5 projection."""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_deduction_execution_postgresql import seed

from app.models.employee import Employee
from app.models.position import Position
from app.models.employment_event import EmploymentEvent
from app.models.payroll_d5_professional_evidence import (
    PayrollD5ProfessionalEvidence,
)

from app.services.payroll_d5_database_field_projection import (
    D5DatabaseProjectionError,
    project_persisted_d5_event,
)


pytestmark = pytest.mark.asyncio


async def setup_case(db, kind="hire"):
    company, actor, contract, *_ = await seed(db)

    token = uuid4().hex
    day = contract.start_date + timedelta(days=2)

    employee = await db.get(
        Employee, contract.employee_id
    )
    assert employee is not None

    employee.tax_number = "1234567890"
    contract.employment_kind = "primary"

    position = Position(
        company_id=company.id,
        code="D5-PROJ-" + token[:12],
        name="Synthetic verified position",
        is_active=True,
        created_by=actor.id,
    )

    db.add(position)
    await db.flush()

    contract.position_id = position.id

    payload = {}

    if kind == "termination":
        payload["termination_basis"] = {
            "ground": "redundancy",
            "labour_code_provision": "40.1",
            "source_reference": "PG-ORDER-" + token,
        }

    event = EmploymentEvent(
        company_id=company.id,
        employment_contract_id=contract.id,
        event_type=kind,
        effective_date=day,
        order_number="PG-EVENT-" + token,
        order_date=day,
        reason="Source-bound D5 E2E",
        request_key="D5-PROJECTION-" + token,
        before_state={},
        after_state={"position_id": position.id},
        request_payload=payload,
        created_by=actor.id,
    )

    db.add(event)
    await db.flush()

    evidence = PayrollD5ProfessionalEvidence(
        company_id=company.id,
        employee_id=employee.id,
        employment_contract_id=contract.id,
        employment_event_id=event.id,
        position_id=position.id,
        effective_from=day,
        effective_to=None,
        documented_on=day,
        professional_name="Synthetic verified profession",
        profession_code="2419.2",
        position_name=position.name,
        employment_category="primary",
        evidence_reference="PG-EVIDENCE-" + token,
        request_key="PG-EVIDENCE-KEY-" + token,
        created_by=actor.id,
    )

    db.add(evidence)
    await db.flush()

    return company, actor, contract, employee, event, evidence


async def test_real_postgresql_projection_and_rejections(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract,
            employee, event, evidence,
        ) = await setup_case(db, "hire")

        result = await project_persisted_d5_event(
            db,
            company_id=company.id,
            employment_event_id=event.id,
        )

        fields = result["candidate"]["fields"]

        assert fields["07"] == 1
        assert fields["08"] == employee.tax_number
        assert fields["10"]["start"] == (
            event.effective_date.strftime("%d%m%Y")
        )
        assert fields["13"] == evidence.profession_code
        assert fields["15"] == evidence.position_name
        assert fields["16"] == event.order_number

        assert result["source_binding"] == (
            "PERSISTED_POSTGRESQL_RECORDS_VERIFIED"
        )
        assert result["official_generation"] == "BLOCKED"
        assert result["official_rows_generated"] == 0

        from app.services.payroll_d5_amendment_reconciliation import (
            D5AmendmentEvidence,
            review_persisted_d5_amendment_reconciliation,
        )

        amendment = D5AmendmentEvidence(
            amendment_number=7,
            effective_date=date(2017, 11, 30),
            source_reference="PG-TEST-AMENDMENT-7",
            source_sha256="7" * 64,
            document_completeness="PARTIAL",
            profession_list_completeness="PARTIAL",
        )

        reconciliation = (
            await review_persisted_d5_amendment_reconciliation(
                db,
                company_id=company.id,
                employment_event_id=event.id,
                amendments=[amendment],
                changes=[],
            )
        )

        assert reconciliation["source_binding"] == (
            "PERSISTED_POSTGRESQL_RECORDS_VERIFIED"
        )
        assert reconciliation[
            "persisted_erp_evidence_sha256"
        ] == result["persisted_erp_evidence_sha256"]
        assert reconciliation["amendment_reconciliation"][
            "internal_consistency"
        ] == "REVIEW_BLOCKED"
        assert reconciliation["official_generation"] == "BLOCKED"
        assert reconciliation["official_rows_generated"] == 0

        with pytest.raises(
            D5DatabaseProjectionError,
            match="not found",
        ):
            await review_persisted_d5_amendment_reconciliation(
                db,
                company_id=company.id + 100000000,
                employment_event_id=event.id,
                amendments=[amendment],
                changes=[],
            )

        from app.services.payroll_d5_database_field_projection import (
            review_persisted_d5_profession_history,
        )
        from app.services.payroll_d5_profession_effective_history import (
            D5ProfessionHistoryChange,
        )

        # Synthetic history tests the implementation;
        # it does not certify this profession's legal history.
        introduced = D5ProfessionHistoryChange(
            profession_code=evidence.profession_code,
            change_kind="introduced",
            effective_date=event.effective_date - timedelta(days=1),
            source_reference="PG-TEST-ONLY",
            source_sha256="a" * 64,
        )

        verified = await review_persisted_d5_profession_history(
            db,
            company_id=company.id,
            employment_event_id=event.id,
            profession_changes=[introduced],
        )

        assert verified["profession_date_review"] == (
            "CANDIDATE_REVIEW"
        )
        assert verified["persisted_erp_evidence_sha256"] == (
            result["persisted_erp_evidence_sha256"]
        )
        assert verified["official_generation"] == "BLOCKED"

        missing = await review_persisted_d5_profession_history(
            db,
            company_id=company.id,
            employment_event_id=event.id,
            profession_changes=[],
        )

        assert missing["profession_date_review"] == (
            "REVIEW_BLOCKED"
        )

        removed = await review_persisted_d5_profession_history(
            db,
            company_id=company.id,
            employment_event_id=event.id,
            profession_changes=[
                introduced,
                D5ProfessionHistoryChange(
                    profession_code=evidence.profession_code,
                    change_kind="removed",
                    effective_date=event.effective_date,
                    source_reference="PG-REMOVAL-TEST",
                    source_sha256="b" * 64,
                ),
            ],
        )

        assert removed["profession_history"][
            "candidate_state"
        ] == "CANDIDATE_INACTIVE"

        with pytest.raises(
            D5DatabaseProjectionError,
            match="not found",
        ):
            await review_persisted_d5_profession_history(
                db,
                company_id=company.id + 100000000,
                employment_event_id=event.id,
                profession_changes=[introduced],
            )

        # Positive/negative date-effective normative candidate
        # coverage is bound to a genuine PostgreSQL HR event.
        # Synthetic source metadata is NOT legal approval.
        from app.services.payroll_d5_database_field_projection import (
            review_persisted_d5_event_period_sources,
        )
        from app.services.payroll_d5_normative_acceptance import (
            D5NormativeSourceCandidate,
            REQUIRED_SOURCE_KINDS,
        )

        def make_source(kind, *, suffix="", offset=0):
            return D5NormativeSourceCandidate(
                document_id=f"{kind}{suffix}",
                source_kind=kind,
                source_url=(
                    f"https://example.org/{kind}{suffix}"
                ),
                content_sha256="a" * 64,
                effective_from=(
                    event.effective_date
                    - timedelta(days=10)
                    + timedelta(days=offset)
                ),
                effective_to=None,
                review_reference="Synthetic source fixture",
            )

        candidates = [
            make_source(kind)
            for kind in sorted(REQUIRED_SOURCE_KINDS)
        ]

        period_review = (
            await review_persisted_d5_event_period_sources(
                db,
                company_id=company.id,
                employment_event_id=event.id,
                normative_candidates=candidates,
            )
        )

        assert period_review["period_source_coverage"] == (
            "COMPLETE_CANDIDATE_COVERAGE"
        )
        assert period_review["source_binding"] == (
            "PERSISTED_POSTGRESQL_RECORDS_VERIFIED"
        )
        assert (
            period_review["persisted_erp_evidence_sha256"]
            == result["persisted_erp_evidence_sha256"]
        )
        assert period_review["normative_mapping"] == (
            "NOT_APPROVED"
        )
        assert period_review["independent_normative_approval"] == (
            "NOT_VERIFIED"
        )
        assert period_review["official_generation"] == (
            "BLOCKED"
        )
        assert period_review["official_rows_generated"] == 0

        incomplete = (
            await review_persisted_d5_event_period_sources(
                db,
                company_id=company.id,
                employment_event_id=event.id,
                normative_candidates=candidates[:-1],
            )
        )
        assert incomplete["period_source_coverage"] == (
            "REVIEW_BLOCKED"
        )
        assert incomplete["normative_source_review"][
            "missing_source_kinds"
        ]

        ambiguous = (
            await review_persisted_d5_event_period_sources(
                db,
                company_id=company.id,
                employment_event_id=event.id,
                normative_candidates=[
                    *candidates,
                    make_source(
                        candidates[0].source_kind,
                        suffix="-alternative",
                    ),
                ],
            )
        )
        assert ambiguous["period_source_coverage"] == (
            "REVIEW_BLOCKED"
        )
        assert ambiguous["normative_source_review"][
            "ambiguous_source_kinds"
        ]

        future = (
            await review_persisted_d5_event_period_sources(
                db,
                company_id=company.id,
                employment_event_id=event.id,
                normative_candidates=[
                    make_source(
                        kind,
                        offset=20,
                    )
                    for kind in sorted(REQUIRED_SOURCE_KINDS)
                ],
            )
        )
        assert future["period_source_coverage"] == (
            "REVIEW_BLOCKED"
        )
        assert len(future["normative_source_review"][
            "missing_source_kinds"
        ]) == len(REQUIRED_SOURCE_KINDS)

        with pytest.raises(
            D5DatabaseProjectionError,
            match="not found",
        ):
            await review_persisted_d5_event_period_sources(
                db,
                company_id=company.id + 100000000,
                employment_event_id=event.id,
                normative_candidates=candidates,
            )

        assert all(
            review["official_generation"] == "BLOCKED"
            for review in (
                period_review, incomplete, ambiguous, future
            )
        )

        print("D5 PG PERIOD SOURCE BINDING = PASS")


        repeat = await project_persisted_d5_event(
            db,
            company_id=company.id,
            employment_event_id=event.id,
        )

        assert (
            repeat["persisted_erp_evidence_sha256"]
            == result["persisted_erp_evidence_sha256"]
        )

        with pytest.raises(
            D5DatabaseProjectionError,
            match="not found",
        ):
            await project_persisted_d5_event(
                db,
                company_id=company.id + 100000000,
                employment_event_id=event.id,
            )

        evidence.employment_category = "external_secondary"
        await db.flush()

        with pytest.raises(
            D5DatabaseProjectionError,
            match="category",
        ):
            await project_persisted_d5_event(
                db,
                company_id=company.id,
                employment_event_id=event.id,
            )

        evidence.employment_category = "primary"
        evidence.effective_from = (
            event.effective_date + timedelta(days=1)
        )
        await db.flush()

        with pytest.raises(
            D5DatabaseProjectionError,
            match="not effective",
        ):
            await project_persisted_d5_event(
                db,
                company_id=company.id,
                employment_event_id=event.id,
            )

        await db.rollback()


async def test_real_postgresql_reversed_event_is_blocked(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        (
            company, actor, contract,
            employee, event, evidence,
        ) = await setup_case(db, "transfer")

        before = await project_persisted_d5_event(
            db,
            company_id=company.id,
            employment_event_id=event.id,
        )

        assert before["candidate"]["fields"]["12"] == 1

        token = uuid4().hex

        db.add(EmploymentEvent(
            company_id=company.id,
            employment_contract_id=contract.id,
            event_type="reversal",
            effective_date=event.effective_date,
            order_number="REVERSAL-" + token,
            order_date=event.effective_date,
            reason="Reverse source event",
            request_key="D5-REVERSAL-" + token,
            before_state={},
            after_state={},
            request_payload={},
            reversal_of_id=event.id,
            created_by=actor.id,
        ))

        await db.flush()

        with pytest.raises(
            D5DatabaseProjectionError,
            match="non-reversed",
        ):
            await project_persisted_d5_event(
                db,
                company_id=company.id,
                employment_event_id=event.id,
            )

        await db.rollback()
