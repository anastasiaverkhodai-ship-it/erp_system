"""Fail-closed checks for the JWT-bound receipt channel."""

import inspect
from types import SimpleNamespace

import pytest
from fastapi.params import Depends as DependsParam

from app.services import payroll_receipt_authenticated_channel as channel

pytestmark = pytest.mark.asyncio


def actor(user_id=1, active=True):
    return SimpleNamespace(id=user_id, is_active=active)


def safe_role(**overrides):
    value = {
        "active_role": "dedicated_receipt_login",
        "login_role": "dedicated_receipt_login",
        "rolcanlogin": True,
        "rolsuper": False,
        "rolbypassrls": False,
        "rolcreaterole": False,
        "rolcreatedb": False,
        "trusted_membership": True,
        "snapshot_owner_membership": False,
        "direct_lifecycle_access": False,
        "direct_evidence_access": False,
    }
    value.update(overrides)
    return value


async def test_company_permission_dependency_exists():
    signature = inspect.signature(
        channel.read_authenticated_receipt_lifecycle
    )

    dependency = signature.parameters[
        "current_user"
    ].default

    assert isinstance(dependency, DependsParam)
    assert callable(dependency.dependency)


@pytest.mark.parametrize(
    "company,user",
    [
        (0,actor()),
        (-1,actor()),
        (True,actor()),
        (1,actor(0)),
        (1,actor(True)),
        (1,actor(active=False)),
        (1,None),
    ],
)
async def test_invalid_identity_blocks(company, user):
    with pytest.raises(channel.ReceiptReadChannelBlocked):
        await channel.read_authenticated_receipt_lifecycle(
            company_id=company,
            current_user=user,
        )


async def test_missing_credentials_block(monkeypatch):
    monkeypatch.delenv(channel.ENV_NAME, raising=False)

    with pytest.raises(
        channel.ReceiptReadChannelBlocked,
        match="not configured",
    ):
        await channel.read_authenticated_receipt_lifecycle(
            company_id=1,
            current_user=actor(),
        )


@pytest.mark.parametrize(
    "username",
    [
        "erp_user",
        "erp_snapshot_owner",
        "erp_receipt_reader",
        "anastasia",
        "postgres",
    ],
)
async def test_unsafe_login_rejected(monkeypatch, username):
    monkeypatch.setenv(
        channel.ENV_NAME,
        "postgresql+asyncpg://"
        + username
        + "@localhost:5432/erp_test",
    )

    with pytest.raises(channel.ReceiptReadChannelBlocked):
        channel._reader_url()


@pytest.mark.parametrize(
    "change",
    [
        {"rolsuper": True},
        {"rolbypassrls": True},
        {"rolcreaterole": True},
        {"rolcreatedb": True},
        {"trusted_membership": False},
        {"snapshot_owner_membership": True},
        {"direct_lifecycle_access": True},
        {"direct_evidence_access": True},
        {"rolcanlogin": False},
        {"login_role": "erp_user"},
    ],
)
async def test_unsafe_role_rejected(change):
    with pytest.raises(channel.ReceiptReadChannelBlocked):
        channel._validate_role(
            safe_role(**change),
            "dedicated_receipt_login",
        )


async def test_valid_login_security_contract():
    channel._validate_role(
        safe_role(),
        "dedicated_receipt_login",
    )


async def test_valid_blocked_response():
    output = {
        "company_id": 1,
        "records": [
            {
                "company_id": 1,
                "official_generation": "BLOCKED",
                "issuer_identity": "NOT_VERIFIED",
                "actual_withholding": "NOT_VERIFIED",
            }
        ],
        "official_generation": "BLOCKED",
        "issuer_identity": "NOT_VERIFIED",
        "actual_withholding": "NOT_VERIFIED",
    }

    assert channel._validate_result(output, 1) == output


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id",2),
        ("official_generation","APPROVED"),
        ("issuer_identity","VERIFIED"),
        ("actual_withholding","VERIFIED"),
    ],
)
async def test_unsafe_response_rejected(field, value):
    output = {
        "company_id": 1,
        "records": [],
        "official_generation": "BLOCKED",
        "issuer_identity": "NOT_VERIFIED",
        "actual_withholding": "NOT_VERIFIED",
    }
    output[field] = value

    with pytest.raises(channel.ReceiptReadChannelBlocked):
        channel._validate_result(output, 1)


async def test_cross_company_row_rejected():
    output = {
        "company_id": 1,
        "records": [{
            "company_id": 2,
            "official_generation": "BLOCKED",
            "issuer_identity": "NOT_VERIFIED",
            "actual_withholding": "NOT_VERIFIED",
        }],
        "official_generation": "BLOCKED",
        "issuer_identity": "NOT_VERIFIED",
        "actual_withholding": "NOT_VERIFIED",
    }

    with pytest.raises(channel.ReceiptReadChannelBlocked):
        channel._validate_result(output, 1)


async def test_no_public_route_registered_here():
    source = inspect.getsource(channel)
    assert "@router." not in source
    assert "@app." not in source
    assert "SET LOCAL ROLE erp_receipt_reader" in source
