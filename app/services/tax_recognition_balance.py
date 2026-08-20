from dataclasses import dataclass
from decimal import Decimal

from app.services.tax_calculation_result import (
    TaxCalculationResult,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
    TaxRecognitionStatus,
)


@dataclass(frozen=True, slots=True)
class TaxRecognitionBalance:
    """
    Aggregated recognition state of a tax calculation.
    """

    calculation: TaxCalculationResult
    method: TaxRecognitionMethod
    recognized_taxable_base: Decimal
    recognized_tax_amount: Decimal

    def __post_init__(self) -> None:
        if self.recognized_taxable_base < 0:
            raise ValueError(
                "Recognized taxable base cannot be negative"
            )

        if self.recognized_tax_amount < 0:
            raise ValueError(
                "Recognized tax amount cannot be negative"
            )

        if (
            self.recognized_taxable_base
            > self.calculation.taxable_base
        ):
            raise ValueError(
                "Recognized taxable base cannot exceed "
                "calculated taxable base"
            )

        if (
            self.recognized_tax_amount
            > self.calculation.tax_amount
        ):
            raise ValueError(
                "Recognized tax amount cannot exceed "
                "calculated tax amount"
            )

    @property
    def remaining_taxable_base(self) -> Decimal:
        return (
            self.calculation.taxable_base
            - self.recognized_taxable_base
        )

    @property
    def remaining_tax_amount(self) -> Decimal:
        return (
            self.calculation.tax_amount
            - self.recognized_tax_amount
        )

    @property
    def status(self) -> TaxRecognitionStatus:
        if (
            self.calculation.taxable_base == 0
            and self.calculation.tax_amount == 0
        ):
            return TaxRecognitionStatus.RECOGNIZED

        if (
            self.recognized_taxable_base == 0
            and self.recognized_tax_amount == 0
        ):
            return TaxRecognitionStatus.PENDING

        if (
            self.recognized_taxable_base
            == self.calculation.taxable_base
            and self.recognized_tax_amount
            == self.calculation.tax_amount
        ):
            return TaxRecognitionStatus.RECOGNIZED

        return TaxRecognitionStatus.PARTIALLY_RECOGNIZED