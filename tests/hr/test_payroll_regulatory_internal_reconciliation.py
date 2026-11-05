from dataclasses import replace
from decimal import Decimal

import pytest

from app.services.payroll_regulatory_internal_reconciliation import (
    InternalRegulatoryRow,
    RegulatoryInternalError,
    build_internal_snapshot,
    verify_internal_snapshot,
)


def row(form="D1", **changes):
    data = dict(
        form=form,
        company_id=1,
        payroll_period="2026-10",
        row_key=f"{form}-source-1",
        source_type="verified_payroll",
        source_id=10,
        source_revision=1,
        document_reference="ERP-SOURCE-1",
        document_date="2026-10-31",
        amounts=(
            {"gross": "100.00"}
            if form in ("D1", "4DF")
            else {}
        ),
        attributes={"source_status": "verified"},
    )
    data.update(changes)
    return InternalRegulatoryRow(**data)


def expected():
    return {
        "D1": {"gross": Decimal("100.00")},
        "4DF": {"gross": Decimal("100.00")},
        "D5": {},
        "D6": {},
    }


def sample_rows():
    return [
        row("D1"),
        row("4DF"),
        row("D5"),
        row("D6"),
    ]


def build(rows=None, totals=None, **kw):
    return build_internal_snapshot(
        company_id=1,
        payroll_period="2026-10",
        version=kw.pop("version", 1),
        rows=sample_rows() if rows is None else rows,
        expected_source_totals=(
            expected() if totals is None else totals
        ),
        **kw,
    )


def test_all_four_forms_internal_snapshot():
    result = build()
    payload = verify_internal_snapshot(result)

    assert result.version == 1
    assert len(result.sha256) == 64
    assert len(payload["rows"]) == 4

    assert payload["official_generation_authorized"] is False
    assert payload["statutory_mapping_verified"] is False
    assert payload["regulator_accepted"] is False


def test_deterministic_hash_ignores_input_row_order():
    first = build()
    second = build(rows=list(reversed(sample_rows())))

    assert first.sha256 == second.sha256


def test_source_total_mismatch_blocked():
    totals = expected()
    totals["D1"]["gross"] = "101.00"

    with pytest.raises(RegulatoryInternalError):
        build(totals=totals)


def test_missing_form_control_blocked():
    totals = expected()
    del totals["D6"]

    with pytest.raises(RegulatoryInternalError):
        build(totals=totals)


def test_missing_financial_amounts_blocked():
    rows = sample_rows()
    rows[0] = replace(rows[0], amounts={})

    with pytest.raises(RegulatoryInternalError):
        build(rows=rows)


def test_tenant_isolation():
    rows = sample_rows()
    rows[0] = replace(rows[0], company_id=2)

    with pytest.raises(RegulatoryInternalError):
        build(rows=rows)


def test_period_isolation():
    rows = sample_rows()
    rows[0] = replace(
        rows[0], payroll_period="2026-09"
    )

    with pytest.raises(RegulatoryInternalError):
        build(rows=rows)


def test_duplicate_row_key_blocked():
    rows = sample_rows()
    rows.append(rows[0])

    with pytest.raises(RegulatoryInternalError):
        build(rows=rows)


def test_correction_uses_latest_revision_only():
    rows = sample_rows()

    corrected = row(
        "D1",
        row_key="D1-correction-2",
        source_revision=2,
        correction_of="D1-source-1",
        amounts={"gross": "125.00"},
    )

    rows.append(corrected)

    totals = expected()
    totals["D1"]["gross"] = "125.00"

    snap = build(rows=rows, totals=totals)

    payload = verify_internal_snapshot(snap)

    assert ["D1", "D1-correction-2"] in (
        payload["active_row_keys"]
    )
    assert ["D1", "D1-source-1"] not in (
        payload["active_row_keys"]
    )


@pytest.mark.parametrize(
    "correction",
    [
        dict(correction_of="missing"),
        dict(
            correction_of="D1-source-1",
            source_id=11,
        ),
        dict(
            correction_of="D1-source-1",
            source_revision=1,
        ),
    ],
)
def test_invalid_corrections_blocked(correction):
    rows = sample_rows()

    rows.append(row(
        "D1",
        row_key="D1-correction",
        **correction,
    ))

    with pytest.raises(RegulatoryInternalError):
        build(rows=rows)


@pytest.mark.parametrize(
    "bad_amount",
    [
        float("nan"),
        1.25,
        "Infinity",
        "1.001",
        "bad",
    ],
)
def test_unsafe_amounts_blocked(bad_amount):
    rows = sample_rows()
    rows[0] = replace(
        rows[0],
        amounts={"gross": bad_amount},
    )

    with pytest.raises(RegulatoryInternalError):
        build(rows=rows)


def test_frozen_prior_snapshot_and_version_chain():
    first = build()

    second = build(
        version=2,
        previous=first,
    )

    result = verify_internal_snapshot(second)

    assert result["previous_sha256"] == first.sha256
    assert first.sha256 != second.sha256

    with pytest.raises(RegulatoryInternalError):
        build(version=4, previous=first)


def test_tampered_snapshot_rejected():
    first = build()
    corrupted = replace(
        first,
        canonical_json=first.canonical_json + " ",
    )

    with pytest.raises(RegulatoryInternalError):
        verify_internal_snapshot(corrupted)


def test_approval_escalation_rejected():
    first = build()

    with pytest.raises(RegulatoryInternalError):
        verify_internal_snapshot(
            replace(
                first,
                official_generation_authorized=True,
            )
        )


def test_snapshot_requires_initial_version_one():
    with pytest.raises(RegulatoryInternalError):
        build(version=2)
