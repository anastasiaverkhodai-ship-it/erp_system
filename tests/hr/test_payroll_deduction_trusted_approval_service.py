"""Fail-closed tests for persisted payroll deduction approval reads."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.payroll_deduction_trusted_approval_service import (
    TrustedApprovalResolutionError,
    resolve_trusted_rule_approval,
)
from app.services.payroll_deduction_rule_approval_contract import (
    RuleApprovalStatus,
)


class FakeScalarResults:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def scalars(self, statement):
        self.calls += 1
        return FakeScalarResults(self.rows)


def approval(**changes):
    data = dict(
        company_id=10,
        legal_category="enforcement_other",
        rule_version="v1",
        legal_reference="Verified reference",
        valid_from=date(2026, 1, 1),
        valid_to=None,
        status="approved",
        created_by=101,
        approved_by=102,
        approved_at=datetime(
            2026, 1, 2, tzinfo=timezone.utc
        ),
        revoked_by=None,
        revoked_at=None,
    )
    data.update(changes)
    return SimpleNamespace(**data)


async def resolve(db, **changes):
    values = dict(
        company_id=10,
        legal_category="enforcement_other",
        rule_version="v1",
        effective_on=date(2026, 10, 13),
    )
    values.update(changes)

    return await resolve_trusted_rule_approval(
        db, **values
    )


@pytest.mark.asyncio
async def test_approved_company_rule_is_resolved():
    db = FakeSession([approval()])

    record = await resolve(db)

    assert record.status is RuleApprovalStatus.APPROVED
    assert record.company_id == 10
    assert record.rule_version == "v1"
    assert db.calls == 1


@pytest.mark.asyncio
async def test_missing_approval_is_rejected():
    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(FakeSession([]))


@pytest.mark.asyncio
async def test_duplicate_approval_is_rejected():
    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(FakeSession([approval(), approval()]))


@pytest.mark.asyncio
async def test_other_company_is_rejected():
    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(
            FakeSession([approval(company_id=20)])
        )


@pytest.mark.asyncio
async def test_wrong_rule_version_is_rejected():
    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(
            FakeSession([approval(rule_version="v2")])
        )


@pytest.mark.asyncio
async def test_pending_approval_is_rejected():
    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(
            FakeSession([
                approval(
                    status="pending",
                    approved_by=None,
                    approved_at=None,
                )
            ])
        )


@pytest.mark.asyncio
async def test_revoked_approval_is_rejected():
    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(
            FakeSession([
                approval(
                    status="revoked",
                    revoked_by=103,
                    revoked_at=datetime(
                        2026, 2, 1,
                        tzinfo=timezone.utc,
                    ),
                )
            ])
        )


@pytest.mark.asyncio
async def test_expired_approval_is_rejected():
    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(
            FakeSession([
                approval(valid_to=date(2026, 6, 30))
            ])
        )


@pytest.mark.asyncio
async def test_future_approval_is_rejected():
    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(
            FakeSession([
                approval(valid_from=date(2026, 12, 1))
            ])
        )


@pytest.mark.asyncio
async def test_self_approval_is_rejected():
    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(
            FakeSession([
                approval(approved_by=101)
            ])
        )


@pytest.mark.asyncio
async def test_unknown_status_is_rejected():
    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(
            FakeSession([
                approval(status="unknown")
            ])
        )


@pytest.mark.asyncio
async def test_invalid_company_fails_before_db_read():
    db = FakeSession([approval()])

    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(db, company_id=0)

    assert db.calls == 0


@pytest.mark.asyncio
async def test_missing_version_fails_before_db_read():
    db = FakeSession([approval()])

    with pytest.raises(TrustedApprovalResolutionError):
        await resolve(db, rule_version="")

    assert db.calls == 0
