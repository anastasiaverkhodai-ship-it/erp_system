
"""Source coverage checks with synthetic fixtures only."""
from dataclasses import replace
from datetime import date
import pytest

from app.services.payroll_d5_source_coverage import (
    D5SourceCoverageError,
    D5SourceCoverageRecord,
    validate_classifier_source_coverage,
)


def complete_synthetic_inventory():
    names = ["base", "amendment_1", "amendment_2"]
    return tuple(
        D5SourceCoverageRecord(
            source_id=name,
            acquired=True,
            content_reviewed=True,
            effective_from=date(2026, 1, 1),
            independently_approved=True,
            sha256="a" * 64,
        )
        for name in names
    )


def test_complete_synthetic_inventory():
    validate_classifier_source_coverage(
        complete_synthetic_inventory(),
        amendment_count=2,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("acquired", False),
        ("content_reviewed", False),
        ("effective_from", None),
        ("independently_approved", False),
        ("sha256", None),
        ("sha256", "invalid"),
    ],
)
def test_unverified_source_refused(field, value):
    records = list(complete_synthetic_inventory())
    records[1] = replace(records[1], **{field: value})
    with pytest.raises(D5SourceCoverageError):
        validate_classifier_source_coverage(
            records, amendment_count=2,
        )


def test_missing_amendment_refused():
    with pytest.raises(D5SourceCoverageError):
        validate_classifier_source_coverage(
            complete_synthetic_inventory()[:-1],
            amendment_count=2,
        )


def test_duplicate_amendment_refused():
    records = complete_synthetic_inventory()
    with pytest.raises(D5SourceCoverageError):
        validate_classifier_source_coverage(
            records + (records[1],),
            amendment_count=2,
        )


def test_unexpected_source_refused():
    records = list(complete_synthetic_inventory())
    records[1] = replace(records[1], source_id="unknown")
    with pytest.raises(D5SourceCoverageError):
        validate_classifier_source_coverage(
            records, amendment_count=2,
        )
