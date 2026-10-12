"""C1-E3 authorized approval service regression tests."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.payroll_deduction_rule_approval import (
    PayrollDeductionRuleApproval,
)
from app.services.payroll_deduction_rule_approval_write_service import (
    APPROVE_PERMISSION,
    CREATE_PERMISSION,
    REVOKE_PERMISSION,
    PayrollDeductionRuleApprovalWriteError,
    approve_rule_approval,
    create_pending_rule_approval,
    revoke_rule_approval,
)


def fake_db(*, permission_granted=True, approval=None):
    db = MagicMock()
    db.execute = AsyncMock()
    db.scalar = AsyncMock(return_value=approval)
    db.flush = AsyncMock()

    permission_result = MagicMock()
    permission_result.scalar_one_or_none.return_value = (
        1 if permission_granted else None
    )
    db.execute.return_value = permission_result
    return db


def existing_approval(
    *,
    status="pending",
    creator=10,
    approver=None,
):
    now = datetime.now(timezone.utc)

    return PayrollDeductionRuleApproval(
        id=55,
        company_id=7,
        legal_category="employer_statutory",
        rule_version="2026-v1",
        legal_reference="Reviewed legal reference",
        valid_from=date(2026, 1, 1),
        valid_to=None,
        status=status,
        created_by=creator,
        created_at=now,
        approved_by=approver,
        approved_at=now if approver else None,
        revoked_by=None,
        revoked_at=None,
    )


@pytest.mark.asyncio
async def test_create_pending_success():
    db = fake_db()

    approval = await create_pending_rule_approval(
        db=db,
        company_id=7,
        legal_category="employer_statutory",
        rule_version="2026-v1",
        legal_reference="Reviewed legal reference",
        valid_from=date(2026, 1, 1),
        valid_to=None,
        actor_id=10,
    )

    assert approval.status == "pending"
    assert approval.company_id == 7
    assert approval.created_by == 10
    assert approval.approved_by is None
    db.add.assert_called_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_denied_without_permission():
    db = fake_db(permission_granted=False)

    with pytest.raises(
        PayrollDeductionRuleApprovalWriteError,
        match="Permission denied",
    ):
        await create_pending_rule_approval(
            db=db,
            company_id=7,
            legal_category="employer_statutory",
            rule_version="2026-v1",
            legal_reference="Reviewed legal reference",
            valid_from=date(2026, 1, 1),
            valid_to=None,
            actor_id=10,
        )

    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_invalid_category_rejected():
    db = fake_db()

    with pytest.raises(
        PayrollDeductionRuleApprovalWriteError
    ):
        await create_pending_rule_approval(
            db=db,
            company_id=7,
            legal_category="invalid_category",
            rule_version="2026-v1",
            legal_reference="Reviewed legal reference",
            valid_from=date(2026, 1, 1),
            valid_to=None,
            actor_id=10,
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_approve_independent_actor_success():
    approval = existing_approval()
    db = fake_db(approval=approval)

    result = await approve_rule_approval(
        db=db,
        company_id=7,
        approval_id=55,
        actor_id=11,
    )

    assert result.status == "approved"
    assert result.approved_by == 11
    assert result.approved_at.tzinfo is not None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_creator_cannot_self_approve():
    approval = existing_approval()
    db = fake_db(approval=approval)

    with pytest.raises(
        PayrollDeductionRuleApprovalWriteError,
        match="Creator cannot approve",
    ):
        await approve_rule_approval(
            db=db,
            company_id=7,
            approval_id=55,
            actor_id=10,
        )

    assert approval.status == "pending"
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_missing_permission():
    approval = existing_approval()
    db = fake_db(permission_granted=False, approval=approval)

    with pytest.raises(
        PayrollDeductionRuleApprovalWriteError,
        match="Permission denied",
    ):
        await approve_rule_approval(
            db=db,
            company_id=7,
            approval_id=55,
            actor_id=11,
        )

    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_wrong_company_not_found():
    db = fake_db(approval=None)

    with pytest.raises(
        PayrollDeductionRuleApprovalWriteError,
        match="not found",
    ):
        await approve_rule_approval(
            db=db,
            company_id=8,
            approval_id=55,
            actor_id=11,
        )


@pytest.mark.asyncio
async def test_approve_twice_rejected():
    approval = existing_approval(
        status="approved",
        approver=11,
    )
    db = fake_db(approval=approval)

    with pytest.raises(
        PayrollDeductionRuleApprovalWriteError,
        match="Only pending",
    ):
        await approve_rule_approval(
            db=db,
            company_id=7,
            approval_id=55,
            actor_id=12,
        )


@pytest.mark.asyncio
async def test_revoke_approved_success():
    approval = existing_approval(
        status="approved",
        approver=11,
    )
    db = fake_db(approval=approval)

    result = await revoke_rule_approval(
        db=db,
        company_id=7,
        approval_id=55,
        actor_id=12,
    )

    assert result.status == "revoked"
    assert result.revoked_by == 12
    assert result.revoked_at.tzinfo is not None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_pending_rejected():
    approval = existing_approval()
    db = fake_db(approval=approval)

    with pytest.raises(
        PayrollDeductionRuleApprovalWriteError,
        match="Only approved",
    ):
        await revoke_rule_approval(
            db=db,
            company_id=7,
            approval_id=55,
            actor_id=12,
        )


@pytest.mark.asyncio
async def test_revoke_missing_permission():
    approval = existing_approval(
        status="approved",
        approver=11,
    )
    db = fake_db(
        permission_granted=False,
        approval=approval,
    )

    with pytest.raises(
        PayrollDeductionRuleApprovalWriteError,
        match="Permission denied",
    ):
        await revoke_rule_approval(
            db=db,
            company_id=7,
            approval_id=55,
            actor_id=12,
        )

    db.scalar.assert_not_awaited()


def test_permissions_are_separate():
    assert len({
        CREATE_PERMISSION,
        APPROVE_PERMISSION,
        REVOKE_PERMISSION,
    }) == 3


def test_service_never_commits_or_rolls_back():
    from pathlib import Path

    source = Path(
        "app/services/payroll_deduction_rule_approval_write_service.py"
    ).read_text()

    assert "await db.commit(" not in source
    assert "await db.rollback(" not in source
