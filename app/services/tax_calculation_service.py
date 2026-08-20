from decimal import Decimal

from app.services.tax_calculation_result import (
    TaxCalculationResult,
)
from app.services.tax_rate_definition import (
    TaxRateDefinition,
)
from app.services.tax_types import (
    TaxDirection,
)


def calculate_tax_amount(
    taxable_base: Decimal,
    tax_rate: TaxRateDefinition,
) -> Decimal:
    if taxable_base < 0:
        raise ValueError(
            "Taxable base cannot be negative"
        )

    return taxable_base * tax_rate.rate


def calculate_tax(
    taxable_base: Decimal,
    tax_rate: TaxRateDefinition,
    direction: TaxDirection,
    currency_code: str,
) -> TaxCalculationResult:
    tax_amount = calculate_tax_amount(
        taxable_base=taxable_base,
        tax_rate=tax_rate,
    )

    return TaxCalculationResult(
        tax_type=tax_rate.tax_type,
        direction=direction,
        tax_rate_code=tax_rate.code,
        tax_rate=tax_rate.rate,
        taxable_base=taxable_base,
        tax_amount=tax_amount,
        currency_code=currency_code,
        treatment=tax_rate.treatment,
    )