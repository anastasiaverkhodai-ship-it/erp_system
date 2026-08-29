from datetime import date
from decimal import Decimal

from app.services.tax_rate_catalog import (
    TaxRateCatalog,
)
from app.services.tax_rate_definition import (
    TaxRateDefinition,
)
from app.services.tax_treatment_types import (
    TaxTreatment,
)
from app.services.tax_types import (
    TaxType,
)


VAT20 = TaxRateDefinition(
    code="VAT20",
    tax_type=TaxType.VAT,
    rate=Decimal("0.20"),
    effective_from=date(
        2011,
        1,
        1,
    ),
    treatment=TaxTreatment.TAXABLE,
)

VAT7 = TaxRateDefinition(
    code="VAT7",
    tax_type=TaxType.VAT,
    rate=Decimal("0.07"),
    effective_from=date(
        2014,
        4,
        1,
    ),
    treatment=TaxTreatment.TAXABLE,
)

VAT14 = TaxRateDefinition(
    code="VAT14",
    tax_type=TaxType.VAT,
    rate=Decimal("0.14"),
    effective_from=date(
        2021,
        3,
        1,
    ),
    treatment=TaxTreatment.TAXABLE,
)

VAT0 = TaxRateDefinition(
    code="VAT0",
    tax_type=TaxType.VAT,
    rate=Decimal("0.00"),
    effective_from=date(
        2011,
        1,
        1,
    ),
    treatment=TaxTreatment.ZERO_RATED,
)


UKRAINIAN_VAT_RATE_CATALOG = TaxRateCatalog(
    (
        VAT20,
        VAT7,
        VAT14,
        VAT0,
    )
)


def validate_ukrainian_vat_rate_catalog() -> None:
    rates = (
        UKRAINIAN_VAT_RATE_CATALOG.all()
    )

    codes = {
        rate.code
        for rate in rates
    }

    if codes != {
        "VAT20",
        "VAT7",
        "VAT14",
        "VAT0",
    }:
        raise ValueError(
            "Unexpected Ukrainian VAT rate catalog"
        )

    if any(
        rate.tax_type != TaxType.VAT
        for rate in rates
    ):
        raise ValueError(
            "Ukrainian VAT catalog contains "
            "a non-VAT tax type"
        )


validate_ukrainian_vat_rate_catalog()
