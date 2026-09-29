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


def test_production_requires_monthly_output_and_expected_total():
    from types import SimpleNamespace
    from decimal import Decimal
    from datetime import date
    import pytest
    from app.services.fixed_asset_depreciation_service import _calculate_amount, FixedAssetDepreciationError
    asset = SimpleNamespace(original_cost=Decimal(10000), salvage_value=Decimal(1000),
        expected_output=None, depreciation_method=FixedAssetDepreciationMethod.PRODUCTION)
    args = dict(asset=asset, accumulated_before=Decimal(0), period_start=date(2026,2,1), period_end=date(2026,2,28))
    with pytest.raises(FixedAssetDepreciationError, match="expected total output"):
        _calculate_amount(**args, actual_output=Decimal(10))
    asset.expected_output = Decimal(1000)
    with pytest.raises(FixedAssetDepreciationError, match="monthly actual output"):
        _calculate_amount(**args)
    assert _calculate_amount(**args, actual_output=Decimal(10)) == Decimal('90.00')


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


def test_annual_methods_use_year_basis_and_cumulative_year_weights():
    from types import SimpleNamespace
    from decimal import Decimal
    from datetime import date
    from app.services.fixed_asset_depreciation_service import _calculate_amount
    asset=SimpleNamespace(original_cost=Decimal(10000),salvage_value=Decimal(1000),useful_life_months=60,
        in_service_date=date(2026,1,1),depreciation_method=FixedAssetDepreciationMethod.DOUBLE_DIMINISHING_BALANCE)
    args=dict(asset=asset,accumulated_before=Decimal('333.33'),period_start=date(2026,3,1),period_end=date(2026,3,31),year_start_carrying=Decimal(10000))
    assert _calculate_amount(**args)==Decimal('333.33')
    asset.depreciation_method=FixedAssetDepreciationMethod.DIMINISHING_BALANCE
    assert _calculate_amount(**args)==Decimal('307.54')
    asset.depreciation_method=FixedAssetDepreciationMethod.CUMULATIVE
    assert _calculate_amount(**args)==Decimal('250.00')
    args.update(period_start=date(2027,2,1),period_end=date(2027,2,28))
    assert _calculate_amount(**args)==Decimal('200.00')
