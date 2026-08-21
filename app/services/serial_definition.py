from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SerialDefinition:
    """
    Individual serial-numbered inventory unit.

    A serial belongs to one company and one product.

    batch_number
        Optional batch / lot to which the serial belongs.
        Used when the product tracking mode is
        BATCH_AND_SERIAL.
    """

    company_id: int
    product_id: int
    serial_number: str
    batch_number: str | None = None

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "Company ID must be greater than zero"
            )

        if self.product_id <= 0:
            raise ValueError(
                "Product ID must be greater than zero"
            )

        if not self.serial_number.strip():
            raise ValueError(
                "Serial number cannot be empty"
            )

        if (
            self.batch_number is not None
            and not self.batch_number.strip()
        ):
            raise ValueError(
                "Batch number cannot be empty when provided"
            )

    @property
    def has_batch(self) -> bool:
        return self.batch_number is not None