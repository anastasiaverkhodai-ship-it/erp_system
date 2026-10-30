"""Statutory generated-row integrity contracts."""
import json
from types import SimpleNamespace as NS

import pytest

from app.services.payroll_regulatory_generated_integrity import (
    validate_mapped_report_rows,
    RegulatoryGeneratedIntegrityError,
)


def sample(kind="d1", code="D1_VERIFIED"):
    payload = {
        "company_id": 1,
        "payroll_period_id": 12,
        "report_kind": kind,
        "normative_mapping_id": 17,
        "source_id": 91,
        "source_sha256": "a" * 64,
    }
    return NS(
        company_id=1,
        row_no=1,
        row_code=code,
        payload_json=json.dumps(payload),
    )


def verify(rows, kind="d1", approvals=frozenset({17})):
    return validate_mapped_report_rows(
        report_kind=kind,
        company_id=1,
        period_id=12,
        rows=rows,
        approved_mapping_ids=approvals,
    )


def test_verified_row_structure():
    assert len(verify([sample()])) == 1


def test_internal_snapshot_is_not_statutory_report():
    with pytest.raises(
        RegulatoryGeneratedIntegrityError,
        match="Internal source evidence",
    ):
        verify([sample(code="SOURCE_EVIDENCE_V1")])


def test_missing_mapping_approval():
    with pytest.raises(
        RegulatoryGeneratedIntegrityError,
        match="independently approved",
    ):
        verify([sample()], approvals=frozenset())


def test_wrong_company():
    row = sample()
    row.company_id = 2
    with pytest.raises(
        RegulatoryGeneratedIntegrityError,
        match="Cross-company",
    ):
        verify([row])


def test_wrong_form():
    with pytest.raises(RegulatoryGeneratedIntegrityError):
        verify([sample()], kind="4df")


def test_duplicate_source():
    rows = [sample(), sample()]
    rows[1].row_no = 2
    with pytest.raises(
        RegulatoryGeneratedIntegrityError,
        match="Duplicate",
    ):
        verify(rows)


def test_missing_source_fingerprint():
    row = sample()
    payload = json.loads(row.payload_json)
    payload["source_sha256"] = ""
    row.payload_json = json.dumps(payload)
    with pytest.raises(
        RegulatoryGeneratedIntegrityError,
        match="fingerprint",
    ):
        verify([row])
