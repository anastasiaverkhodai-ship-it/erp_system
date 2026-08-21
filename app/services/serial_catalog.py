from app.services.serial_definition import (
    SerialDefinition,
)


class SerialCatalogError(Exception):
    """Base error for serial catalog operations."""


class SerialNotFoundError(SerialCatalogError):
    """Raised when a serial number cannot be found."""


class DuplicateSerialError(SerialCatalogError):
    """Raised when the same serial number is registered twice."""


class SerialCatalog:
    def __init__(
        self,
        serials: tuple[SerialDefinition, ...],
    ) -> None:
        self._by_key: dict[
            tuple[int, int, str],
            SerialDefinition,
        ] = {}

        for serial in serials:
            key = (
                serial.company_id,
                serial.product_id,
                serial.serial_number,
            )

            if key in self._by_key:
                raise DuplicateSerialError(
                    "Duplicate serial number: "
                    f"company_id={serial.company_id}, "
                    f"product_id={serial.product_id}, "
                    f"serial_number='{serial.serial_number}'"
                )

            self._by_key[key] = serial

    def get(
        self,
        company_id: int,
        product_id: int,
        serial_number: str,
    ) -> SerialDefinition:
        key = (
            company_id,
            product_id,
            serial_number,
        )

        try:
            return self._by_key[key]
        except KeyError as exc:
            raise SerialNotFoundError(
                "Serial number not found: "
                f"company_id={company_id}, "
                f"product_id={product_id}, "
                f"serial_number='{serial_number}'"
            ) from exc

    def for_product(
        self,
        company_id: int,
        product_id: int,
    ) -> tuple[SerialDefinition, ...]:
        return tuple(
            serial
            for serial in self._by_key.values()
            if (
                serial.company_id == company_id
                and serial.product_id == product_id
            )
        )

    def for_batch(
        self,
        company_id: int,
        product_id: int,
        batch_number: str,
    ) -> tuple[SerialDefinition, ...]:
        return tuple(
            serial
            for serial in self._by_key.values()
            if (
                serial.company_id == company_id
                and serial.product_id == product_id
                and serial.batch_number == batch_number
            )
        )

    def all(
        self,
    ) -> tuple[SerialDefinition, ...]:
        return tuple(self._by_key.values())