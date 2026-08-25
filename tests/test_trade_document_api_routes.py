from app.main import app


BASE_PATH = (
    "/api/v1/companies/"
    "{company_id}/trade-documents"
)

DETAIL_PATH = (
    BASE_PATH
    + "/{document_id}"
)


def _paths() -> dict:
    return app.openapi()["paths"]


def test_trade_document_base_routes() -> None:
    paths = _paths()

    assert BASE_PATH in paths
    assert "get" in paths[BASE_PATH]
    assert "post" in paths[BASE_PATH]


def test_trade_document_detail_routes() -> None:
    paths = _paths()

    assert DETAIL_PATH in paths
    assert "get" in paths[DETAIL_PATH]
    assert "patch" in paths[DETAIL_PATH]


def test_trade_document_has_no_hard_delete() -> None:
    paths = _paths()

    assert (
        "delete"
        not in paths[DETAIL_PATH]
    )


def test_trade_document_has_only_expected_paths() -> None:
    trade_paths = {
        path
        for path in _paths()
        if "/trade-documents" in path
    }

    assert trade_paths == {
        BASE_PATH,
        DETAIL_PATH,
    }


def test_trade_document_status_not_patchable_in_openapi() -> None:
    schema = app.openapi()

    update_schema = (
        schema["components"]["schemas"]
        ["TradeDocumentUpdate"]
    )

    assert (
        "status"
        not in update_schema["properties"]
    )
