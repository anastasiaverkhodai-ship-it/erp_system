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


@pytest.mark.asyncio
async def test_active_company_reviewer_authorized(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services import (
        payroll_deduction_rule_approval_write_service as permissions,
    )
    from app.services.payroll_severance_independent_review_service import (
        require_severance_reviewer_permission,
    )

    db = SimpleNamespace(
        scalar=AsyncMock(return_value=11)
    )
    check = AsyncMock()

    monkeypatch.setattr(
        permissions, "_require_permission", check
    )

    await require_severance_reviewer_permission(
        db,
        company_id=1,
        reviewer_id=11,
    )

    check.assert_awaited_once_with(
        db=db,
        company_id=1,
        actor_id=11,
        permission="journal_entries.approve",
    )


@pytest.mark.asyncio
async def test_inactive_company_reviewer_rejected(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services import (
        payroll_deduction_rule_approval_write_service as permissions,
    )
    from app.services.payroll_severance_independent_review_service import (
        require_severance_reviewer_permission,
    )

    db = SimpleNamespace(
        scalar=AsyncMock(return_value=None)
    )
    check = AsyncMock()

    monkeypatch.setattr(
        permissions, "_require_permission", check
    )

    with pytest.raises(
        SeveranceIndependentReviewError,
        match="Active reviewer",
    ):
        await require_severance_reviewer_permission(
            db,
            company_id=1,
            reviewer_id=11,
        )

    check.assert_not_awaited()


@pytest.mark.asyncio
async def test_reviewer_without_company_permission_rejected(
    monkeypatch,
):
    from unittest.mock import AsyncMock
    from app.services import (
        payroll_deduction_rule_approval_write_service as permissions,
    )
    from app.services.payroll_deduction_rule_approval_write_service import (
        PayrollDeductionRuleApprovalWriteError,
    )
    from app.services.payroll_severance_independent_review_service import (
        require_severance_reviewer_permission,
    )

    db = SimpleNamespace(
        scalar=AsyncMock(return_value=11)
    )

    monkeypatch.setattr(
        permissions,
        "_require_permission",
        AsyncMock(
            side_effect=PayrollDeductionRuleApprovalWriteError(
                "Permission denied for this company"
            )
        ),
    )

    with pytest.raises(
        SeveranceIndependentReviewError,
        match="journal_entries.approve",
    ):
        await require_severance_reviewer_permission(
            db,
            company_id=1,
            reviewer_id=11,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id,reviewer_id",
    [
        (0, 11),
        (1, 0),
        (True, 11),
        (1, True),
        (-1, 11),
    ],
)
async def test_invalid_company_reviewer_identity(
    company_id,
    reviewer_id,
):
    from unittest.mock import AsyncMock
    from app.services.payroll_severance_independent_review_service import (
        require_severance_reviewer_permission,
    )

    db = SimpleNamespace(
        scalar=AsyncMock()
    )

    with pytest.raises(SeveranceIndependentReviewError):
        await require_severance_reviewer_permission(
            db,
            company_id=company_id,
            reviewer_id=reviewer_id,
        )

    db.scalar.assert_not_awaited()
