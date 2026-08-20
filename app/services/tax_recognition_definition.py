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
class TaxRecognitionDefinition:
    """
    Recognition state of a calculated tax amount.

    calculation
        Original tax calculation result.

    method
        Method that determines when tax is recognized.

    recognized_amount
        Part of the calculated tax amount that has
        already been recognized.
    """

    calculation: TaxCalculationResult
    method: TaxRecognitionMethod
    recognized_amount: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.recognized_amount < 0:
            raise ValueError(
                "Recognized tax amount cannot be negative"
            )

        if (
            self.recognized_amount
            > self.calculation.tax_amount
        ):
            raise ValueError(
                "Recognized tax amount cannot exceed "
                "calculated tax amount"
            )

    @property
    def remaining_amount(self) -> Decimal:
        return (
            self.calculation.tax_amount
            - self.recognized_amount
        )

    @property
    def status(self) -> TaxRecognitionStatus:
        if self.calculation.tax_amount == 0:
            return TaxRecognitionStatus.RECOGNIZED

        if self.recognized_amount == 0:
            return TaxRecognitionStatus.PENDING

        if (
            self.recognized_amount
            == self.calculation.tax_amount
        ):
            return TaxRecognitionStatus.RECOGNIZED

        return TaxRecognitionStatus.PARTIALLY_RECOGNIZED