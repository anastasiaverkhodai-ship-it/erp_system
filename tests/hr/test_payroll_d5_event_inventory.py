"""Internal D5/D6 employment event source contract."""

from datetime import date

import pytest
from sqlalchemy import Column, Date, Integer, MetaData, String, Table
from sqlalchemy.dialects import postgresql

from app.services.payroll_d5_event_inventory import (
    D5EventInventoryError,
    SUPPORTED_EVENTS,
    active_employment_event_query,
    validate_d5_event_record,
)
from app.services.payroll_regulatory_d5_d6_source_control import (
    SOURCE_TABLES,
    prepare_source_evidence,
)


DAY = date(2026, 9, 30)


def row(kind="hire"):
    payload = {}

    if kind == "termination":
        payload["termination_basis"] = {
            "ground": "redundancy",
            "labour_code_provision": "40.1",
            "source_reference": "DOCUMENTED-TEST-ORDER",
        }

    return dict(
        id=11,
        company_id=1,
        employment_contract_id=20,
        event_type=kind,
        effective_date=DAY,
        order_date=DAY,
        order_number="HR-ORDER-11",
        reason="Documented HR event",
        request_key="HR-REQUEST-11",
        before_state={"position_id": 1},
        after_state={"position_id": 1},
        request_payload=payload,
        reversal_of_id=None,
    )


@pytest.mark.parametrize(
    "kind",
    ["hire", "transfer", "termination"],
)
def test_supported_active_event(kind):
    assert validate_d5_event_record(row(kind))["event_type"] == kind


def test_source_inventory_includes_hr_events():
    assert "employment_events" in SOURCE_TABLES
    assert len(SOURCE_TABLES) == 4
    assert SUPPORTED_EVENTS == {
        "hire", "transfer", "termination"
    }


@pytest.mark.parametrize(
    "kind",
    ["reversal", "unknown"],
)
def test_reversal_and_unknown_rejected(kind):
    with pytest.raises(
        D5EventInventoryError,
        match="Unsupported HR event",
    ):
        validate_d5_event_record(row(kind))


def test_reversal_reference_rejected():
    item = row()
    item["reversal_of_id"] = 10

    with pytest.raises(
        D5EventInventoryError,
        match="Reversal cannot enter",
    ):
        validate_d5_event_record(item)


def test_missing_termination_basis_rejected():
    item = row("termination")
    item["request_payload"] = {}

    with pytest.raises(
        D5EventInventoryError,
        match="Termination legal basis missing",
    ):
        validate_d5_event_record(item)


def test_invalid_termination_provision_rejected():
    item = row("termination")
    item["request_payload"]["termination_basis"][
        "labour_code_provision"
    ] = "38"

    with pytest.raises(
        D5EventInventoryError,
        match="Invalid termination legal basis",
    ):
        validate_d5_event_record(item)


def test_termination_basis_on_transfer_rejected():
    item = row("termination")
    item["event_type"] = "transfer"

    with pytest.raises(
        D5EventInventoryError,
        match="Nontermination has termination basis",
    ):
        validate_d5_event_record(item)


def test_event_source_remains_review_only():
    result = prepare_source_evidence(
        table_name="employment_events",
        records=[row("termination")],
        company_id=1,
        period_start=date(2026, 9, 1),
        period_end=DAY,
    )

    assert result["dated_in_period_count"] == 1
    assert result["undated_or_ambiguous_count"] == 0

    evidence = result["dated_evidence"][0]

    assert evidence["event_date"] == DAY.isoformat()
    assert evidence["date_field"] == "effective_date"
    assert evidence["review_only"] is True
    assert evidence["statutory_classification"] == "NOT_VERIFIED"
    assert len(evidence["source_sha256"]) == 64


def test_event_source_digest_changes_on_source_change():
    original = row()
    changed = row()
    changed["order_number"] = "DIFFERENT-ORDER"

    def fingerprint(item):
        return prepare_source_evidence(
            table_name="employment_events",
            records=[item],
            company_id=1,
            period_start=DAY,
            period_end=DAY,
        )["dated_evidence"][0]["source_sha256"]

    assert fingerprint(original) != fingerprint(changed)


def test_query_has_tenant_scope_and_reversal_antijoin():
    table = Table(
        "employment_events",
        MetaData(),
        Column("id", Integer),
        Column("company_id", Integer),
        Column("event_type", String),
        Column("reversal_of_id", Integer),
        Column("effective_date", Date),
    )

    statement = active_employment_event_query(
        table,
        company_id=1,
        limit=100,
    )

    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "NOT (EXISTS" in sql
    assert "d5_inventory_reversal" in sql
    assert "company_id = 1" in sql
    assert "reversal_of_id IS NULL" in sql
    assert "hire" in sql
    assert "termination" in sql


def test_invalid_tenant_rejected():
    table = Table(
        "employment_events",
        MetaData(),
        Column("id", Integer),
        Column("company_id", Integer),
        Column("event_type", String),
        Column("reversal_of_id", Integer),
    )

    with pytest.raises(
        D5EventInventoryError,
        match="Valid company",
    ):
        active_employment_event_query(
            table,
            company_id=0,
            limit=10,
        )
