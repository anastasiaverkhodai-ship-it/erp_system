from pathlib import Path

from fastapi.routing import APIRoute

from app.api.v1.payments import (
    router as payments_router,
)
from app.main import app


LOCAL_BASE = (
    "/companies/{company_id}/payments"
)

API_BASE = (
    "/api/v1/companies/{company_id}/payments"
)


def payment_routes():
    return [
        route
        for route in payments_router.routes
        if isinstance(
            route,
            APIRoute,
        )
    ]


def route_exists(
    path,
    method,
):
    return any(
        route.path == path
        and method in route.methods
        for route in payment_routes()
    )


def test_payment_router_prefix():
    assert (
        payments_router.prefix
        == LOCAL_BASE
    )


def test_payment_crud_lifecycle_routes():
    assert route_exists(
        LOCAL_BASE,
        "GET",
    )

    assert route_exists(
        LOCAL_BASE,
        "POST",
    )

    assert route_exists(
        LOCAL_BASE + "/{payment_id}",
        "GET",
    )

    assert route_exists(
        LOCAL_BASE
        + "/{payment_id}/confirm",
        "POST",
    )

    assert route_exists(
        LOCAL_BASE
        + "/{payment_id}/cancel",
        "POST",
    )


def test_payment_settlement_routes():
    settlements = (
        LOCAL_BASE
        + "/{payment_id}/settlements"
    )

    assert route_exists(
        settlements,
        "GET",
    )

    assert route_exists(
        settlements,
        "POST",
    )

    assert route_exists(
        settlements
        + "/{allocation_id}/reverse",
        "POST",
    )

    assert route_exists(
        LOCAL_BASE
        + "/{payment_id}/reconciliation",
        "GET",
    )


def test_payment_routes_exposed_by_app():
    paths = app.openapi()[
        "paths"
    ]

    assert API_BASE in paths

    assert {
        "get",
        "post",
    } <= set(
        paths[API_BASE]
    )

    reconciliation = (
        API_BASE
        + "/{payment_id}/reconciliation"
    )

    assert (
        reconciliation
        in paths
    )


def test_payment_api_permissions():
    text = Path(
        "app/api/v1/payments.py"
    ).read_text()

    assert (
        '"payments.read"'
        in text
    )

    assert (
        '"payments.manage"'
        in text
    )

    assert (
        '"payments.settlements.read"'
        in text
    )

    assert (
        '"payments.settlements.manage"'
        in text
    )
