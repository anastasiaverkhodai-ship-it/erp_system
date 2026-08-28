from pathlib import Path

from fastapi.routing import APIRoute

from app.api.v1.counterparty_open_items import (
    router as counterparty_open_items_router,
)
from app.main import app


LOCAL_BASE = (
    "/companies/{company_id}/"
    "counterparty-open-items"
)

API_BASE = (
    "/api/v1/companies/{company_id}/"
    "counterparty-open-items"
)


def open_item_routes():
    """
    FastAPI 0.141+ stores included routers in the parent app as
    _IncludedRouter objects instead of flattening every APIRoute
    directly into app.routes.

    Inspect the source APIRouter for route metadata and use generated
    OpenAPI for proof that the router is exposed through the final app.
    """
    return [
        route
        for route in counterparty_open_items_router.routes
        if isinstance(
            route,
            APIRoute,
        )
    ]


def test_open_item_router_has_expected_prefix():
    assert (
        counterparty_open_items_router.prefix
        == LOCAL_BASE
    )


def test_open_item_list_route_exists():
    matches = [
        route
        for route in open_item_routes()
        if (
            route.path == LOCAL_BASE
            and "GET" in route.methods
        )
    ]

    assert len(matches) == 1


def test_open_item_get_route_exists():
    matches = [
        route
        for route in open_item_routes()
        if (
            route.path
            == LOCAL_BASE + "/{open_item_id}"
            and "GET" in route.methods
        )
    ]

    assert len(matches) == 1


def test_open_item_routes_are_exposed_by_final_app():
    schema = app.openapi()

    assert (
        API_BASE
        in schema["paths"]
    )

    assert (
        "get"
        in schema["paths"][API_BASE]
    )

    get_one_path = (
        API_BASE
        + "/{open_item_id}"
    )

    assert (
        get_one_path
        in schema["paths"]
    )

    assert (
        "get"
        in schema["paths"][get_one_path]
    )


def test_open_item_list_exposes_expected_filters():
    route = next(
        route
        for route in open_item_routes()
        if (
            route.path == LOCAL_BASE
            and "GET" in route.methods
        )
    )

    query_names = {
        param.alias
        for param in route.dependant.query_params
    }

    assert {
        "item_type",
        "status",
        "counterparty_id",
        "contract_id",
        "currency_code",
        "document_date_from",
        "document_date_to",
        "due_date_from",
        "due_date_to",
    } <= query_names


def test_open_item_api_uses_read_permission():
    text = Path(
        "app/api/v1/"
        "counterparty_open_items.py"
    ).read_text()

    assert (
        text.count(
            '"counterparty_open_items.read"'
        )
        == 2
    )


def test_open_item_response_has_no_fake_balance_fields():
    text = Path(
        "app/schemas/"
        "counterparty_open_item.py"
    ).read_text()

    for forbidden in (
        "open_amount:",
        "paid_amount:",
        "settled_amount:",
        "balance_due:",
    ):
        assert forbidden not in text
