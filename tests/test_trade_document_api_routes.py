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
