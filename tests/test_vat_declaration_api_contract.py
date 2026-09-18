from pathlib import Path

from fastapi.routing import APIRoute

from app.api.v1.vat_declarations import router
from app.main import app
from app.services.vat_declaration_orchestration_service import (
    VAT_DECLARATION_BUILD_OPERATION,
    VAT_DECLARATION_RESULT_TYPE,
)


ROOT = "/companies/{company_id}/vat-declarations"
DETAIL = ROOT + "/{declaration_id}"
EVENTS = DETAIL + "/status-events"

API_ROOT = "/api/v1" + ROOT
API_DETAIL = "/api/v1" + DETAIL
API_EVENTS = "/api/v1" + EVENTS


def test_raw_router_contract():
    routes = {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert (ROOT, "POST") in routes
    assert (ROOT, "GET") in routes
    assert (DETAIL, "GET") in routes
    assert (EVENTS, "POST") in routes
    assert (EVENTS, "GET") in routes


def test_openapi_registration_contract():
    paths = app.openapi()["paths"]

    assert "post" in paths[API_ROOT]
    assert "get" in paths[API_ROOT]
    assert "get" in paths[API_DETAIL]
    assert "post" in paths[API_EVENTS]
    assert "get" in paths[API_EVENTS]


def test_openapi_request_key_contract():
    operation = app.openapi()["paths"][
        API_ROOT
    ]["post"]

    parameters = operation.get(
        "parameters",
        [],
    )

    assert any(
        parameter.get("name") == "Idempotency-Key"
        and parameter.get("in") == "header"
        for parameter in parameters
    )


def test_idempotency_catalog():
    assert (
        VAT_DECLARATION_BUILD_OPERATION
        == "vat_declaration_build"
    )

    assert (
        VAT_DECLARATION_RESULT_TYPE
        == "vat_declaration"
    )


def test_orchestration_has_no_transaction_ownership():
    text = Path(
        "app/services/"
        "vat_declaration_orchestration_service.py"
    ).read_text(encoding="utf-8")

    assert ".commit(" not in text
    assert ".rollback(" not in text


def test_api_owns_transaction():
    text = Path(
        "app/api/v1/vat_declarations.py"
    ).read_text(encoding="utf-8")

    assert "await db.commit()" in text
    assert "await db.rollback()" in text


def test_permission_contract():
    text = Path(
        "app/api/v1/vat_declarations.py"
    ).read_text(encoding="utf-8")

    assert "journal_entries.approve" in text
    assert "journal_entries.read" in text


def test_lifecycle_exact_call_contract():
    text = Path(
        "app/api/v1/vat_declarations.py"
    ).read_text(encoding="utf-8")

    assert "append_vat_declaration_status(" in text
    assert "db=db" in text
    assert "new_status=data.status" in text


def test_build_exact_call_contract():
    text = Path(
        "app/services/"
        "vat_declaration_orchestration_service.py"
    ).read_text(encoding="utf-8")

    assert (
        "build_vat_declaration_snapshot(\n"
        "        db=session,"
        in text
    )


def test_main_registration_source_contract():
    text = Path(
        "app/main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.api.v1.vat_declarations "
        "import router as vat_declarations_router"
        in text
    )

    assert (
        'app.include_router('
        'vat_declarations_router, prefix="/api/v1")'
        in text
    )
