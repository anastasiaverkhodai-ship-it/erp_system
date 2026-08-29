from app.main import app


BASE_PATH = (
    "/api/v1/companies/"
    "{company_id}/trade-documents"
)

DETAIL_PATH = (
    BASE_PATH
    + "/{document_id}"
)

CONFIRM_PATH = (
    DETAIL_PATH
    + "/confirm"
)

CANCEL_PATH = (
    DETAIL_PATH
    + "/cancel"
)

FULFILL_PATH = (
    f"{DETAIL_PATH}/fulfill"
)

FULFILLMENT_REVERSE_PATH = (
    f"{DETAIL_PATH}/fulfillments/"
    "{fulfillment_id}/reverse"
)


INVOICE_DETAIL_PATH = (
    BASE_PATH
    + "/{invoice_id}"
)

ALLOCATION_PATH = (
    INVOICE_DETAIL_PATH
    + "/fulfillment-allocations"
)

ALLOCATION_REVERSE_PATH = (
    ALLOCATION_PATH
    + "/{allocation_id}/reverse"
)

RECONCILIATION_PATH = (
    INVOICE_DETAIL_PATH
    + "/reconciliation"
)


def _paths():
    return app.openapi()["paths"]


def test_trade_document_collection_routes() -> None:
    paths = _paths()

    assert BASE_PATH in paths

    assert {
        "get",
        "post",
    } <= set(
        paths[BASE_PATH]
    )


def test_trade_document_detail_routes() -> None:
    paths = _paths()

    assert DETAIL_PATH in paths

    assert {
        "get",
        "patch",
    } <= set(
        paths[DETAIL_PATH]
    )


def test_trade_document_has_no_hard_delete() -> None:
    paths = _paths()

    assert (
        "delete"
        not in paths[DETAIL_PATH]
    )


def test_sales_order_confirm_route() -> None:
    paths = _paths()

    assert CONFIRM_PATH in paths

    assert (
        "post"
        in paths[CONFIRM_PATH]
    )


def test_sales_order_cancel_route() -> None:
    paths = _paths()

    assert CANCEL_PATH in paths

    assert (
        "post"
        in paths[CANCEL_PATH]
    )


def test_only_expected_trade_document_paths() -> None:
    trade_paths = {
        path
        for path
        in _paths()
        if (
            "/trade-documents"
            in path
        )
    }

    assert trade_paths == {
        BASE_PATH,
        DETAIL_PATH,
        CONFIRM_PATH,
        CANCEL_PATH,
        FULFILL_PATH,
        FULFILLMENT_REVERSE_PATH,
        ALLOCATION_PATH,
        ALLOCATION_REVERSE_PATH,
        RECONCILIATION_PATH,
    }


def test_status_not_user_editable() -> None:
    schema = (
        app.openapi()
        ["components"]
        ["schemas"]
        ["TradeDocumentUpdate"]
    )

    assert (
        "status"
        not in schema.get(
            "properties",
            {},
        )
    )
