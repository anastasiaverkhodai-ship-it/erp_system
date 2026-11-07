import pytest

from app.services.payroll_d5_verified_review_snapshot import (
    D5ReviewSnapshotError,
    _digest,
    _targets,
    build_d5_review_snapshot,
    verify_d5_review_snapshot,
)


def test_digest_is_deterministic():
    assert _digest({"a": 1, "b": 2}) == _digest(
        {"b": 2, "a": 1}
    )


def test_digest_detects_mutation():
    assert _digest({"a": 1}) != _digest({"a": 2})


def test_duplicate_target_rejected():
    event = {
        "kind": "event",
        "employment_event_id": 5,
    }
    with pytest.raises(
        D5ReviewSnapshotError,
        match="Duplicate",
    ):
        _targets([event, event])


def test_unknown_target_rejected():
    with pytest.raises(D5ReviewSnapshotError):
        _targets([{"kind": "unknown"}])


def test_boolean_identifier_rejected():
    with pytest.raises(D5ReviewSnapshotError):
        _targets([{
            "kind": "event",
            "employment_event_id": True,
        }])


@pytest.mark.asyncio
async def test_invalid_company_rejected_before_db():
    class ForbiddenDB:
        pass

    with pytest.raises(D5ReviewSnapshotError):
        await build_d5_review_snapshot(
            ForbiddenDB(),
            company_id=0,
            targets=[{
                "kind": "event",
                "employment_event_id": 1,
            }],
        )


@pytest.mark.asyncio
async def test_malformed_snapshot_rejected():
    class ForbiddenDB:
        pass

    with pytest.raises(D5ReviewSnapshotError):
        await verify_d5_review_snapshot(
            ForbiddenDB(),
            {"body": {}, "snapshot_sha256": "wrong"},
        )
