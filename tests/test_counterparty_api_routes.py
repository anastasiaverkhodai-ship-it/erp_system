from app.main import app


BASE = (
    "/api/v1/companies/"
    "{company_id}/counterparties"
)

DETAIL = (
    BASE
    + "/{counterparty_id}"
)


def _paths() -> dict:
    return app.openapi()["paths"]


def test_counterparty_list_and_create_routes() -> None:
    paths = _paths()

    assert BASE in paths
    assert "get" in paths[BASE]
    assert "post" in paths[BASE]


def test_counterparty_detail_routes() -> None:
    paths = _paths()

    assert DETAIL in paths
    assert "get" in paths[DETAIL]
    assert "patch" in paths[DETAIL]


def test_counterparty_has_no_hard_delete() -> None:
    paths = _paths()

    assert "delete" not in paths[DETAIL]


def test_counterparty_openapi_paths_unique() -> None:
    paths = _paths()

    matching = {
        path
        for path in paths
        if path.startswith(BASE)
    }

    assert matching == {
        BASE,
        DETAIL,
    }
