from datetime import date
from decimal import Decimal

import pytest

from app.services.tax_rate_catalog import (
    TaxRateNotFoundError,
)
from app.services.tax_treatment_types import (
    TaxTreatment,
)
from app.services.tax_types import (
    TaxType,
)
from app.services.ukrainian_vat_rate_catalog import (
    UKRAINIAN_VAT_RATE_CATALOG,
    validate_ukrainian_vat_rate_catalog,
)


def test_ukrainian_vat_catalog_validation():
    validate_ukrainian_vat_rate_catalog()


def test_ukrainian_vat_catalog_codes():
    assert {
        rate.code
        for rate
        in UKRAINIAN_VAT_RATE_CATALOG.all()
    } == {
        "VAT20",
        "VAT7",
        "VAT14",
        "VAT0",
    }


def test_vat20_definition():
    rate = (
        UKRAINIAN_VAT_RATE_CATALOG
        .get_effective(
            "VAT20",
            date(2026, 8, 29),
        )
    )

    assert rate.tax_type == TaxType.VAT
    assert rate.rate == Decimal("0.20")
    assert (
        rate.treatment
        == TaxTreatment.TAXABLE
    )


def test_vat7_definition():
    rate = (
        UKRAINIAN_VAT_RATE_CATALOG
        .get_effective(
            "VAT7",
            date(2026, 8, 29),
        )
    )

    assert rate.rate == Decimal("0.07")
    assert (
        rate.effective_from
        == date(2014, 4, 1)
    )


def test_vat14_definition():
    rate = (
        UKRAINIAN_VAT_RATE_CATALOG
        .get_effective(
            "VAT14",
            date(2026, 8, 29),
        )
    )

    assert rate.rate == Decimal("0.14")
    assert (
        rate.effective_from
        == date(2021, 3, 1)
    )


def test_vat0_is_zero_rated():
    rate = (
        UKRAINIAN_VAT_RATE_CATALOG
        .get_effective(
            "VAT0",
            date(2026, 8, 29),
        )
    )

    assert rate.rate == Decimal("0.00")
    assert (
        rate.treatment
        == TaxTreatment.ZERO_RATED
    )


def test_vat14_not_available_before_effective_date():
    with pytest.raises(
        TaxRateNotFoundError
    ):
        (
            UKRAINIAN_VAT_RATE_CATALOG
            .get_effective(
                "VAT14",
                date(2021, 2, 28),
            )
        )


def test_vat7_not_available_before_effective_date():
    with pytest.raises(
        TaxRateNotFoundError
    ):
        (
            UKRAINIAN_VAT_RATE_CATALOG
            .get_effective(
                "VAT7",
                date(2014, 3, 31),
            )
        )
