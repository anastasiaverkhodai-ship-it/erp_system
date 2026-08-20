from dataclasses import dataclass
from decimal import Decimal

from app.services.tax_treatment_types import (
    TaxTreatment,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


@dataclass(frozen=True, slots=True)
class TaxCalculationResult:
    """
    Immutable result of a tax calculation.
    """

    tax_type: TaxType
    direction: TaxDirection
    tax_rate_code: str
    tax_rate: Decimal
    taxable_base: Decimal
    tax_amount: Decimal
    currency_code: str
    treatment: TaxTreatment = TaxTreatment.TAXABLE

    def __post_init__(self) -> None:
        if not self.tax_rate_code.strip():
            raise ValueError(
                "Tax rate code cannot be empty"
            )

        if self.tax_rate < 0 or self.tax_rate > 1:
            raise ValueError(
                "Tax rate must be between 0 and 1"
            )

        if self.taxable_base < 0:
            raise ValueError(
                "Taxable base cannot be negative"
            )

        if self.tax_amount < 0:
            raise ValueError(
                "Tax amount cannot be negative"
            )

        if (
            len(self.currency_code) != 3
            or not self.currency_code.isalpha()
            or self.currency_code != self.currency_code.upper()
        ):
            raise ValueError(
                "Currency code must contain exactly "
                "3 uppercase letters"
            )

        if (
            self.treatment == TaxTreatment.TAXABLE
            and self.tax_rate == 0
        ):
            raise ValueError(
                "Taxable treatment requires "
                "a positive tax rate"
            )

        if (
            self.treatment
            in {
                TaxTreatment.ZERO_RATED,
                TaxTreatment.EXEMPT,
                TaxTreatment.OUT_OF_SCOPE,
            }
            and self.tax_rate != 0
        ):
            raise ValueError(
                f"{self.treatment.value} treatment "
                "requires a zero tax rate"
            )