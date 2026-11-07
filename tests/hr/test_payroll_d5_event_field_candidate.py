from dataclasses import replace
from datetime import date

import pytest

from app.services.payroll_d5_event_field_candidate import (
    D5CategoryChangeFacts,
    D5EventFacts,
    D5FieldCandidateError,
    project_d5_category_change_candidate,
    project_d5_event_candidate,
)


SHA = "a" * 64


def facts(kind="hire", category=1):
    return D5EventFacts(
        company_id=1,
        employee_id=2,
        contract_id=3,
        employment_event_id=4,
        event_kind=kind,
        effective_date=date(2026, 10, 15),
        person_category=category,
        confirmed_identity="1234567890",
        full_name="Test Employee",
        profession_code="2419.2",
        profession_name="Test Profession",
        position_title="Test Position",
        order_reference="ORDER-001",
        source_sha256=SHA,
        termination_basis=(
            "Documented basis"
            if kind == "termination"
            else None
        ),
    )


def test_hire_candidate_fields():
    result = project_d5_event_candidate(facts())

    assert result["fields"]["07"] == 1
    assert result["fields"]["10"]["start"] == "15102026"
    assert result["fields"]["10"]["end"] is None
    assert result["fields"]["12"] == 0
    assert result["official_row"] is False
    assert result["official_generation"] == "BLOCKED"


def test_transfer_candidate():
    result = project_d5_event_candidate(
        facts("transfer")
    )

    assert result["fields"]["12"] == 1
    assert result["fields"]["16"] == "ORDER-001"
    assert result["fields"]["17"] is None


def test_termination_basis_required():
    with pytest.raises(
        D5FieldCandidateError,
        match="termination_basis",
    ):
        project_d5_event_candidate(
            replace(
                facts("termination"),
                termination_basis=None,
            )
        )


def test_termination_end_date():
    result = project_d5_event_candidate(
        facts("termination")
    )

    assert result["fields"]["10"]["end"] == "15102026"
    assert result["fields"]["17"] == "Documented basis"


def test_missing_identity_rejected():
    with pytest.raises(
        D5FieldCandidateError,
        match="confirmed_identity",
    ):
        project_d5_event_candidate(
            replace(
                facts(),
                confirmed_identity="",
            )
        )


def test_missing_profession_rejected():
    with pytest.raises(
        D5FieldCandidateError,
        match="profession_code",
    ):
        project_d5_event_candidate(
            replace(
                facts(),
                profession_code="",
            )
        )


def test_invalid_event_type_rejected():
    with pytest.raises(
        D5FieldCandidateError,
        match="Unsupported event",
    ):
        project_d5_event_candidate(
            replace(
                facts(),
                event_kind="reversal",
            )
        )


def test_invalid_source_hash_rejected():
    with pytest.raises(
        D5FieldCandidateError,
        match="SHA256",
    ):
        project_d5_event_candidate(
            replace(
                facts(),
                source_sha256="not-a-hash",
            )
        )


def test_primary_secondary_change_two_rows():
    old = facts("transfer", 1)
    new = facts("transfer", 2)

    result = project_d5_category_change_candidate(
        D5CategoryChangeFacts(
            previous=old,
            current=new,
            previous_end_date=date(2026, 10, 15),
            current_start_date=date(2026, 10, 15),
        )
    )

    rows = result["candidate_rows"]

    assert result["candidate_count"] == 2
    assert [r["fields"]["07"] for r in rows] == [
        1, 2
    ]
    assert [r["fields"]["12"] for r in rows] == [
        1, 1
    ]
    assert rows[0]["fields"]["10"]["end"] == (
        "15102026"
    )
    assert rows[1]["fields"]["10"]["start"] == (
        "15102026"
    )
    assert result["official_generation"] == "BLOCKED"
    assert result["official_rows_generated"] == 0


def test_same_category_rejected():
    with pytest.raises(
        D5FieldCandidateError,
        match="Category did not change",
    ):
        project_d5_category_change_candidate(
            D5CategoryChangeFacts(
                previous=facts("transfer", 1),
                current=facts("transfer", 1),
                previous_end_date=date(2026, 10, 15),
                current_start_date=date(2026, 10, 15),
            )
        )


def test_cross_employee_change_rejected():
    with pytest.raises(
        D5FieldCandidateError,
        match="identity mismatch",
    ):
        project_d5_category_change_candidate(
            D5CategoryChangeFacts(
                previous=facts("transfer", 1),
                current=replace(
                    facts("transfer", 2),
                    employee_id=99,
                ),
                previous_end_date=date(2026, 10, 15),
                current_start_date=date(2026, 10, 15),
            )
        )


def test_candidate_digest_deterministic():
    a = project_d5_event_candidate(facts())
    b = project_d5_event_candidate(facts())

    assert a["candidate_sha256"] == (
        b["candidate_sha256"]
    )
    assert len(a["candidate_sha256"]) == 64
