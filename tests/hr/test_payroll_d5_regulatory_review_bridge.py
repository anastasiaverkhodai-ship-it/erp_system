import pytest

from app.services.payroll_d5_regulatory_review_bridge import (
    D5RegulatoryBridgeError,
    _positive,
    _digest,
    _workflow_digest,
    build_d5_regulatory_review_bridge,
    verify_d5_regulatory_review_bridge,
)


def test_bridge_digest_deterministic():
    assert _digest({"x": 1, "y": 2}) == _digest(
        {"y": 2, "x": 1}
    )


def test_bridge_digest_detects_changes():
    assert _digest({"x": 1}) != _digest({"x": 2})


@pytest.mark.parametrize(
    "value", (0, -1, True, None, "5")
)
def test_invalid_identifiers(value):
    with pytest.raises(D5RegulatoryBridgeError):
        _positive(value, "company_id")


def test_missing_regulatory_digest_fails():
    with pytest.raises(
        D5RegulatoryBridgeError
    ):
        _workflow_digest({})


@pytest.mark.asyncio
async def test_invalid_company_before_db_access():
    with pytest.raises(D5RegulatoryBridgeError):
        await build_d5_regulatory_review_bridge(
            object(),
            company_id=0,
            payroll_period_id=1,
            targets=[{
                "kind": "event",
                "employment_event_id": 1,
            }],
        )


@pytest.mark.asyncio
async def test_invalid_bridge_envelope():
    with pytest.raises(D5RegulatoryBridgeError):
        await verify_d5_regulatory_review_bridge(
            object(), {"body": {}}
        )
