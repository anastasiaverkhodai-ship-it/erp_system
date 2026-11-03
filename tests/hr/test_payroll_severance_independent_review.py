from datetime import date
from types import SimpleNamespace

import pytest

from app.services.payroll_severance_independent_review_service import (
    SeveranceIndependentReviewError,
    validate_independent_review,
)


def data():
    proposal = SimpleNamespace(
        id=17,
        company_id=1,
        submitted_by=10,
        status="review_required",
        document_date=date(2026, 11, 3),
    )
    return {
        "proposal": proposal,
        "reviewer_id": 11,
        "review_reference": "INDEPENDENT-REVIEW-DOCUMENT-001",
        "review_date": date(2026, 11, 3),
        "decision": "reviewed_pending_statutory",
        "request_key": "independent-review-001",
    }


def test_independent_review_record_valid():
    result = validate_independent_review(**data())

    assert result["reviewer_id"] == 11
    assert result["decision"] == "reviewed_pending_statutory"


def test_rejected_review_supported():
    args = data()
    args["decision"] = "rejected"

    result = validate_independent_review(**args)
    assert result["decision"] == "rejected"


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewer_id", 10),
        ("reviewer_id", 0),
        ("reviewer_id", True),
        ("review_reference", "short"),
        ("review_date", date(2026, 11, 2)),
        ("review_date", "2026-11-03"),
        ("decision", "approved"),
        ("decision", "posted"),
        ("request_key", "short"),
    ],
)
def test_invalid_review_rejected(field, value):
    args = data()
    args[field] = value

    with pytest.raises(SeveranceIndependentReviewError):
        validate_independent_review(**args)


def test_inactive_proposal_rejected():
    args = data()
    args["proposal"].status = "approved"

    with pytest.raises(SeveranceIndependentReviewError):
        validate_independent_review(**args)
