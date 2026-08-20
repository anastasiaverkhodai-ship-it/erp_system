from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.tax_treatment_types import (
    TaxTreatment,
)
from app.services.tax_types import (
    TaxType,
)


@dataclass(frozen=True, slots=True)
class TaxRateDefinition:
    """
    Effective-dated tax rate definition.

    rate
        Decimal fraction:
        0.20 = 20%
        0.07 = 7%
        0.00 = 0%

    treatment
        Determines the tax treatment represented
        by this rate definition.
    """

    code: str
    tax_type: TaxType
    rate: Decimal
    effective_from: date
    treatment: TaxTreatment = TaxTreatment.TAXABLE

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError(
                "Tax rate code cannot be empty"
            )

        if self.rate < 0 or self.rate > 1:
            raise ValueError(
                "Tax rate must be between 0 and 1"
            )

        if (
            self.treatment == TaxTreatment.TAXABLE
            and self.rate == 0
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
            and self.rate != 0
        ):
            raise ValueError(
                f"{self.treatment.value} treatment "
                "requires a zero tax rate"
            )