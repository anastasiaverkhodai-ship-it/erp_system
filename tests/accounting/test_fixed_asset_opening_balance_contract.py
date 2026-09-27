from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.fixed_asset_opening_balance import (
    FixedAssetOpeningBalance,
)
from app.schemas.opening_balance_detail import (
    OpeningDetailsCreate,
    OpeningFixedAssetLine,
)


def make_line(
    *,
    fixed_asset_id=1,
    acquisition_date=date(2020, 1, 1),
    in_service_date=date(2020, 2, 1),
    original_cost=Decimal("100000.00"),
    accumulated_depreciation=Decimal("25000.00"),
):
    return OpeningFixedAssetLine(
        fixed_asset_id=fixed_asset_id,
        acquisition_date=acquisition_date,
        in_service_date=in_service_date,
        original_cost=original_cost,
        accumulated_depreciation=accumulated_depreciation,
    )


def test_fixed_asset_opening_model_table():
    assert (
        FixedAssetOpeningBalance.__tablename__
        == "fixed_asset_opening_balances"
    )


def test_fixed_asset_opening_model_contract():
    columns = {
        column.name
        for column
        in FixedAssetOpeningBalance.__table__.columns
    }

    assert {
        "id",
        "company_id",
        "opening_balance_id",
        "fixed_asset_id",
        "acquisition_date",
        "in_service_date",
        "original_cost",
        "accumulated_depreciation",
        "previous_status",
        "previous_acquisition_date",
        "previous_in_service_date",
        "previous_original_cost",
        "created_by",
        "created_at",
    } <= columns


def test_fixed_asset_opening_accepts_valid_line():
    details = OpeningDetailsCreate(
        fixed_assets=[
            make_line()
        ]
    )

    assert len(details.fixed_assets) == 1
    assert (
        details.fixed_assets[0].original_cost
        == Decimal("100000.00")
    )


def test_fixed_asset_opening_accepts_zero_accumulated_depreciation():
    details = OpeningDetailsCreate(
        fixed_assets=[
            make_line(
                accumulated_depreciation=Decimal("0.00")
            )
        ]
    )

    assert (
        details.fixed_assets[0].accumulated_depreciation
        == Decimal("0.00")
    )


def test_fixed_asset_opening_rejects_accumulated_above_cost():
    with pytest.raises(ValidationError):
        make_line(
            original_cost=Decimal("100.00"),
            accumulated_depreciation=Decimal("100.01"),
        )


def test_fixed_asset_opening_rejects_negative_accumulated():
    with pytest.raises(ValidationError):
        make_line(
            accumulated_depreciation=Decimal("-0.01")
        )


def test_fixed_asset_opening_rejects_zero_cost():
    with pytest.raises(ValidationError):
        make_line(
            original_cost=Decimal("0.00")
        )


def test_fixed_asset_opening_rejects_service_before_acquisition():
    with pytest.raises(ValidationError):
        make_line(
            acquisition_date=date(2020, 2, 1),
            in_service_date=date(2020, 1, 31),
        )


def test_fixed_asset_opening_rejects_duplicate_asset():
    with pytest.raises(ValidationError):
        OpeningDetailsCreate(
            fixed_assets=[
                make_line(
                    fixed_asset_id=10
                ),
                make_line(
                    fixed_asset_id=10
                ),
            ]
        )


def test_existing_stock_only_detail_still_valid():
    from app.schemas.opening_balance_detail import (
        OpeningStockLine,
    )

    details = OpeningDetailsCreate(
        stock=[
            OpeningStockLine(
                product_id=1,
                warehouse_id=1,
                quantity=Decimal("2.0000"),
                unit_cost=Decimal("10.0000"),
            )
        ]
    )

    assert len(details.stock) == 1
    assert details.fixed_assets == []


def test_empty_detail_remains_invalid():
    with pytest.raises(ValidationError):
        OpeningDetailsCreate()
