from pathlib import Path
import ast

from app.models.fixed_asset import (
    FixedAssetDepreciationMethod,
)
from app.models.fixed_asset_depreciation import (
    FixedAssetDepreciation,
)


def test_depreciation_model_contract():
    table = FixedAssetDepreciation.__table__

    assert table.name == "fixed_asset_depreciations"

    names = set(table.c.keys())

    assert {
        "id",
        "company_id",
        "fixed_asset_id",
        "request_key",
        "period_start",
        "period_end",
        "posting_date",
        "method",
        "amount",
        "accumulated_before",
        "accumulated_after",
        "reversal_of_id",
        "created_by",
        "created_at",
    } <= names


def test_supported_method_set_is_unchanged():
    assert {
        item.value
        for item in FixedAssetDepreciationMethod
    } == {
        "straight_line",
        "diminishing_balance",
        "double_diminishing_balance",
        "cumulative",
        "production",
    }


def test_service_owns_no_transaction_boundary():
    path = Path(
        "app/services/"
        "fixed_asset_depreciation_service.py"
    )

    tree = ast.parse(path.read_text())

    forbidden = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func

        if (
            isinstance(func, ast.Attribute)
            and func.attr in {"commit", "rollback"}
        ):
            forbidden.append(func.attr)

    assert forbidden == []


def test_service_uses_canonical_posting_and_reversal():
    text = Path(
        "app/services/"
        "fixed_asset_depreciation_service.py"
    ).read_text()

    assert "post_journal_entry" in text
    assert "reverse_journal_entry" in text
    assert "JournalEntry(" in text
    assert "JournalEntryLine(" in text


def test_production_is_explicitly_blocked_without_basis():
    text = Path(
        "app/services/"
        "fixed_asset_depreciation_service.py"
    ).read_text()

    assert (
        "production depreciation requires "
        in text
    )
    assert "production quantity foundation" in text


def test_salvage_floor_is_explicit():
    text = Path(
        "app/services/"
        "fixed_asset_depreciation_service.py"
    ).read_text()

    assert "salvage floor" in text


def test_journal_has_depreciation_provenance():
    text = Path(
        "app/models/journal_entry.py"
    ).read_text()

    assert "fixed_asset_depreciation_id" in text
