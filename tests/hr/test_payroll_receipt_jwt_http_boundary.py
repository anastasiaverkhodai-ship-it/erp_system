"""HTTP/JWT receipt-reader boundary regression.

Uses actual JWT decoding and FastAPI dependency resolution.
Authentication SQL and privileged reader SQL are replaced by
explicit test doubles. This is not production database E2E.
"""

import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.services import payroll_receipt_authenticated_channel as channel


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeAuthDB:
    def __init__(self, state):
        self.state = state

    async def execute(self, statement, *_args, **_kwargs):
        sql = str(statement).lower()

        if "from users" in sql:
            user_id = self.state["user_id"]

            if not self.state["active"]:
                return ScalarResult(None)

            return ScalarResult(
                SimpleNamespace(
                    id=user_id,
                    is_active=True,
                )
            )

        if (
            "role_permissions" in sql
            and "user_company_roles" in sql
            and "permissions" in sql
        ):
            if self.state["allowed"]:
                return ScalarResult(10)
            return ScalarResult(None)

        raise AssertionError(
            "Unexpected authentication SQL: " + sql
        )


class RoleResult:
    def mappings(self):
        return self

    def one_or_none(self):
        return {
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


class FakeConnection:
    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def begin(self):
        return self

    async def execute(self, statement, *_args, **_kwargs):
        sql = str(statement)

        if sql.strip() == "SET LOCAL ROLE erp_receipt_reader":
            self.state["role_set"] = True
            return None

        if "session_user" not in sql:
            raise AssertionError("Unexpected role-check SQL")

        return RoleResult()

    async def scalar(self, statement, params=None):
        sql = str(statement)

        if "SELECT current_user" in sql:
            return "erp_receipt_reader"

        if "p13_receipt_tenant_review" not in sql:
            raise AssertionError("Unexpected reader SQL")

        self.state["reader_call"] = dict(params)

        company_id = params["company_id"]

        return {
            "company_id": company_id,
            "records": [{
                "company_id": company_id,
                "official_generation": "BLOCKED",
                "issuer_identity": "NOT_VERIFIED",
                "actual_withholding": "NOT_VERIFIED",
            }],
            "official_generation": "BLOCKED",
            "issuer_identity": "NOT_VERIFIED",
            "actual_withholding": "NOT_VERIFIED",
        }


class FakeEngine:
    def __init__(self, state):
        self.state = state

    def connect(self):
        return FakeConnection(self.state)

    async def dispose(self):
        return None


def token_for(
    user_id=1,
    *,
    kind="access",
    expired=False,
    secret=None,
):
    payload = {
        "sub": str(user_id),
        "type": kind,
        "exp": int(time.time()) + (
            -3600 if expired else 3600
        ),
    }

    return jwt.encode(
        payload,
        settings.jwt_secret if secret is None else secret,
        algorithm=settings.jwt_algorithm,
    )


@pytest.fixture
def harness(monkeypatch):
    state = {
        "user_id": 1,
        "active": True,
        "allowed": True,
        "reader_call": None,
    }

    app = FastAPI()

    @app.get("/test/companies/{company_id}/receipts")
    async def review(
        result=Depends(
            channel.read_authenticated_receipt_lifecycle
        ),
    ):
        return result

    async def fake_get_db():
        yield FakeAuthDB(state)

    app.dependency_overrides[get_db] = fake_get_db

    monkeypatch.setenv(
        channel.ENV_NAME,
        "postgresql+asyncpg://"
        "dedicated_receipt_login:unused@"
        "localhost:5432/erp_test",
    )

    monkeypatch.setattr(
        channel,
        "create_async_engine",
        lambda *_args, **_kwargs: FakeEngine(state),
    )

    with TestClient(
        app,
        raise_server_exceptions=False,
    ) as client:
        yield client, state


def request(client, bearer, company=1, suffix=""):
    headers = {}

    if bearer is not None:
        headers["Authorization"] = "Bearer " + bearer

    return client.get(
        f"/test/companies/{company}/receipts{suffix}",
        headers=headers,
    )


def test_valid_jwt_binds_server_actor(harness):
    client, state = harness

    response = request(client, token_for(1), 1)

    assert response.status_code == 200
    assert state["reader_call"]["actor_id"] == 1
    assert state["reader_call"]["company_id"] == 1
    assert response.json()["official_generation"] == "BLOCKED"


def test_authorized_other_company(harness):
    client, state = harness
    state["user_id"] = 2

    response = request(client, token_for(2), 2)

    assert response.status_code == 200
    assert state["reader_call"]["actor_id"] == 2
    assert state["reader_call"]["company_id"] == 2


def test_client_actor_spoof_does_not_change_identity(harness):
    client, state = harness

    response = request(
        client,
        token_for(1),
        1,
        "?actor_id=999&authenticated_actor_id=999",
    )

    assert response.status_code == 200
    assert state["reader_call"]["actor_id"] == 1


def test_missing_bearer_denied(harness):
    client, state = harness

    response = request(client, None)

    assert response.status_code == 401
    assert state["reader_call"] is None


def test_invalid_token_denied(harness):
    client, state = harness

    response = request(client, "invalid-token")

    assert response.status_code == 401
    assert state["reader_call"] is None


def test_expired_token_denied(harness):
    client, state = harness

    response = request(
        client,
        token_for(expired=True),
    )

    assert response.status_code == 401
    assert state["reader_call"] is None


def test_wrong_signature_denied(harness):
    client, state = harness

    response = request(
        client,
        token_for(secret="incorrect-signing-secret"),
    )

    assert response.status_code == 401
    assert state["reader_call"] is None


def test_wrong_token_type_denied(harness):
    client, state = harness

    response = request(
        client,
        token_for(kind="refresh"),
    )

    assert response.status_code == 401
    assert state["reader_call"] is None


def test_inactive_user_denied(harness):
    client, state = harness
    state["active"] = False

    response = request(client, token_for(1))

    assert response.status_code == 401
    assert state["reader_call"] is None


def test_missing_company_permission_denied(harness):
    client, state = harness
    state["allowed"] = False

    response = request(client, token_for(1))

    assert response.status_code == 403
    assert state["reader_call"] is None


def test_denied_company_with_spoofed_actor(harness):
    client, state = harness
    state["allowed"] = False

    response = request(
        client,
        token_for(1),
        company=2,
        suffix="?actor_id=2",
    )

    assert response.status_code == 403
    assert state["reader_call"] is None


def test_bad_company_id_cannot_trigger_reader(harness):
    client, state = harness

    response = request(
        client,
        token_for(1),
        company=0,
    )

    assert response.status_code != 200
    assert state["reader_call"] is None


def test_jwt_subject_not_integer_denied(harness):
    client, state = harness

    response = request(
        client,
        token_for("not-an-integer"),
    )

    assert response.status_code == 401
    assert state["reader_call"] is None


def test_no_approval_flags_in_http_response(harness):
    client, _state = harness

    response = request(client, token_for(1))

    assert response.status_code == 200

    body = response.json()

    assert body["issuer_identity"] == "NOT_VERIFIED"
    assert body["actual_withholding"] == "NOT_VERIFIED"
    assert body["official_generation"] == "BLOCKED"

    for item in body["records"]:
        assert item["issuer_identity"] == "NOT_VERIFIED"
        assert item["actual_withholding"] == "NOT_VERIFIED"
        assert item["official_generation"] == "BLOCKED"
