from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.vat_declaration_aggregation_service import (
    aggregate_vat_declaration_sources,
)
from app.services.vat_declaration_carry_forward_service import (
    OpeningCarryTranche,
    apply_fifo_negative_carry,
)
from app.services.vat_declaration_persistence_service import (
    _source_fk_kwargs,
)
from app.services.vat_declaration_source_loader_service import (
    VatDeclarationSource,
)


def source(
    *,
    kind="output_tax_recognition",
    source_id=1,
    direction="output",
    base="100.00",
    tax="20.00",
):
    return VatDeclarationSource(
        source_kind=kind,
        source_id=source_id,
        economic_effective_date=date(2026, 9, 1),
        direction=direction,
        taxable_base_delta=Decimal(base),
        tax_amount_delta=Decimal(tax),
        currency_code="UAH",
    )


def test_aggregation_separates_output_and_input():
    totals = aggregate_vat_declaration_sources(
        [
            source(),
            source(
                kind="input_tax_recognition",
                source_id=2,
                direction="input",
                base="40.00",
                tax="8.00",
            ),
        ]
    )

    assert totals.output_taxable_base == Decimal("100.00")
    assert totals.output_vat == Decimal("20.00")
    assert totals.input_taxable_base == Decimal("40.00")
    assert totals.input_vat_credit == Decimal("8.00")


def test_aggregation_preserves_signed_corrections():
    totals = aggregate_vat_declaration_sources(
        [
            source(),
            source(
                kind="sales_return",
                source_id=2,
                direction="output",
                base="-25.00",
                tax="-5.00",
            ),
        ]
    )

    assert totals.output_taxable_base == Decimal("75.00")
    assert totals.output_vat == Decimal("15.00")


def test_negative_period_correction_preserves_signed_totals():
    totals=aggregate_vat_declaration_sources([source(kind="sales_return",base="-25",tax="-5")])
    assert totals.output_vat==Decimal('-5')
    carry=apply_fifo_negative_carry(output_vat=totals.output_vat,input_vat_credit=Decimal(0),opening_tranches=[])
    assert carry.closing_negative_carry==Decimal(5)


def test_fifo_consumes_oldest_origin_first():
    result = apply_fifo_negative_carry(
        output_vat=Decimal("100.00"),
        input_vat_credit=Decimal("20.00"),
        opening_tranches=[
            OpeningCarryTranche(
                source_declaration_id=10,
                origin_reporting_year=2026,
                origin_reporting_month=2,
                opening_amount=Decimal("30.00"),
            ),
            OpeningCarryTranche(
                source_declaration_id=11,
                origin_reporting_year=2026,
                origin_reporting_month=1,
                opening_amount=Decimal("20.00"),
            ),
        ],
    )

    assert result.opening_negative_carry == Decimal("50.00")
    assert result.tranches[0].origin_reporting_month == 1
    assert result.tranches[0].consumed_amount == Decimal("20.00")
    assert result.tranches[1].consumed_amount == Decimal("30.00")
    assert result.vat_payable == Decimal("30.00")
    assert result.current_period_negative == Decimal("0.00")
    assert result.closing_negative_carry == Decimal("0.00")


def test_prior_carry_is_not_consumed_when_current_period_negative():
    result = apply_fifo_negative_carry(
        output_vat=Decimal("10.00"),
        input_vat_credit=Decimal("30.00"),
        opening_tranches=[
            OpeningCarryTranche(
                source_declaration_id=10,
                origin_reporting_year=2026,
                origin_reporting_month=1,
                opening_amount=Decimal("40.00"),
            )
        ],
    )

    assert result.opening_negative_carry == Decimal("40.00")
    assert result.tranches[0].consumed_amount == Decimal("0.00")
    assert result.tranches[0].closing_amount == Decimal("40.00")
    assert result.vat_payable == Decimal("0.00")
    assert result.current_period_negative == Decimal("20.00")
    assert result.closing_negative_carry == Decimal("60.00")


def test_zero_net_does_not_consume_prior_carry():
    result = apply_fifo_negative_carry(
        output_vat=Decimal("20.00"),
        input_vat_credit=Decimal("20.00"),
        opening_tranches=[
            OpeningCarryTranche(
                source_declaration_id=10,
                origin_reporting_year=2026,
                origin_reporting_month=1,
                opening_amount=Decimal("15.00"),
            )
        ],
    )

    assert result.tranches[0].consumed_amount == Decimal("0.00")
    assert result.closing_negative_carry == Decimal("15.00")


def test_fifo_rejects_duplicate_origin():
    with pytest.raises(HTTPException):
        apply_fifo_negative_carry(
            output_vat=Decimal("20.00"),
            input_vat_credit=Decimal("0.00"),
            opening_tranches=[
                OpeningCarryTranche(
                    source_declaration_id=10,
                    origin_reporting_year=2026,
                    origin_reporting_month=1,
                    opening_amount=Decimal("5.00"),
                ),
                OpeningCarryTranche(
                    source_declaration_id=11,
                    origin_reporting_year=2026,
                    origin_reporting_month=1,
                    opening_amount=Decimal("5.00"),
                ),
            ],
        )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (
            "output_tax_recognition",
            "tax_recognition_event_id",
        ),
        (
            "input_tax_recognition",
            "tax_recognition_event_id",
        ),
        (
            "sales_return",
            "sales_return_recognition_event_id",
        ),
        (
            "sales_value_correction",
            "trade_value_correction_event_id",
        ),
        (
            "purchase_return_input_credit_correction",
            (
                "purchase_return_input_vat_credit_"
                "correction_event_id"
            ),
        ),
        (
            "purchase_value_input_credit_correction",
            (
                "purchase_value_correction_input_vat_credit_"
                "correction_event_id"
            ),
        ),
    ],
)
def test_source_kind_maps_to_exact_typed_fk(
    kind,
    expected,
):
    values = _source_fk_kwargs(
        source(
            kind=kind,
            direction=(
                "output"
                if kind
                in {
                    "output_tax_recognition",
                    "sales_return",
                    "sales_value_correction",
                }
                else "input"
            ),
        )
    )

    populated = {
        key
        for key, value in values.items()
        if value is not None
    }

    assert populated == {expected}


def test_services_do_not_own_commit_or_rollback():
    paths = [
        Path(
            "app/services/"
            "vat_declaration_source_loader_service.py"
        ),
        Path(
            "app/services/"
            "vat_declaration_aggregation_service.py"
        ),
        Path(
            "app/services/"
            "vat_declaration_carry_forward_service.py"
        ),
        Path(
            "app/services/"
            "vat_declaration_persistence_service.py"
        ),
        Path(
            "app/services/"
            "vat_declaration_lifecycle_service.py"
        ),
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert ".commit(" not in text
        assert ".rollback(" not in text


def test_persistence_uses_period_lock_for_version_serialization():
    text = Path(
        "app/services/"
        "vat_declaration_persistence_service.py"
    ).read_text(encoding="utf-8")

    assert ".with_for_update()" in text
    assert "snapshot_version + 1" in text
    assert "supersedes_declaration_id" in text


def test_current_period_negative_is_not_persisted_as_self_carry_line():
    text = Path(
        "app/services/"
        "vat_declaration_persistence_service.py"
    ).read_text(encoding="utf-8")

    assert (
        "source_declaration_id=("
        in text
    )
    assert (
        "source_declaration_id=declaration.id"
        not in text
    )
