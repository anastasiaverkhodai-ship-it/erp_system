from pathlib import Path

from fastapi.routing import APIRoute

from app.api.v1.general_ledger import router
from app.main import app


ROOT = "/companies/{company_id}"
GL = ROOT + "/general-ledger"
CARD = ROOT + "/accounts/{account_id}/card"

API_GL = "/api/v1" + GL
API_CARD = "/api/v1" + CARD

API_SOURCE = Path(
    "app/api/v1/general_ledger.py"
)
MAIN_SOURCE = Path("app/main.py")


def _routes():
    return {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def test_gl_router_prefix():
    assert router.prefix == ROOT


def test_raw_gl_routes():
    routes = _routes()

    assert (GL, "GET") in routes
    assert (CARD, "GET") in routes


def test_openapi_registration():
    paths = app.openapi()["paths"]

    assert API_GL in paths
    assert API_CARD in paths

    assert "get" in paths[API_GL]
    assert "get" in paths[API_CARD]


def test_gl_query_contract():
    operation = app.openapi()["paths"][
        API_GL
    ]["get"]

    parameters = {
        parameter["name"]: parameter
        for parameter in operation.get(
            "parameters",
            [],
        )
    }

    assert {
        "company_id",
        "date_from",
        "date_to",
        "account_id",
        "account_code",
    } <= set(parameters)

    assert (
        parameters["date_from"]["required"]
        is True
    )
    assert (
        parameters["date_to"]["required"]
        is True
    )
    assert (
        parameters["account_id"]["required"]
        is False
    )
    assert (
        parameters["account_code"]["required"]
        is False
    )


def test_account_card_query_contract():
    operation = app.openapi()["paths"][
        API_CARD
    ]["get"]

    parameters = {
        parameter["name"]: parameter
        for parameter in operation.get(
            "parameters",
            [],
        )
    }

    assert {
        "company_id",
        "account_id",
        "date_from",
        "date_to",
    } <= set(parameters)

    assert (
        parameters["date_from"]["required"]
        is True
    )
    assert (
        parameters["date_to"]["required"]
        is True
    )


def test_gl_api_reuses_journal_read_permission():
    source = API_SOURCE.read_text(
        encoding="utf-8"
    )

    assert (
        source.count(
            '"journal_entries.read"'
        )
        == 2
    )


def test_gl_api_is_read_only():
    source = API_SOURCE.read_text(
        encoding="utf-8"
    )

    assert "await db.commit()" not in source
    assert "await db.rollback()" not in source
    assert "db.add(" not in source
    assert "db.delete(" not in source


def test_gl_api_calls_canonical_services():
    source = API_SOURCE.read_text(
        encoding="utf-8"
    )

    assert "get_general_ledger(" in source
    assert "get_account_card(" in source

    assert "JournalEntry(" not in source
    assert "JournalEntryLine(" not in source


def test_main_registration_source_contract():
    source = MAIN_SOURCE.read_text(
        encoding="utf-8"
    )

    assert (
        "from app.api.v1.general_ledger "
        "import router as general_ledger_router"
        in source
    )

    assert (
        "general_ledger_router,"
        in source
    )


def test_no_mutation_methods_on_gl_routes():
    paths = app.openapi()["paths"]

    assert set(paths[API_GL]) == {
        "get"
    }
    assert set(paths[API_CARD]) == {
        "get"
    }
