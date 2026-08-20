from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CurrencyDefinition:
    """
    Immutable metadata describing a currency.

    code
        ISO 4217 alphabetic currency code, for example UAH or EUR.

    numeric_code
        ISO 4217 numeric currency code.

    name
        Human-readable currency name.

    symbol
        Currency symbol used in UI and documents.

    minor_units
        Number of decimal places normally used for monetary amounts.
        For example, UAH and EUR use 2.
    """

    code: str
    numeric_code: str
    name: str
    symbol: str
    minor_units: int

    def __post_init__(self) -> None:
        if len(self.code) != 3 or not self.code.isalpha():
            raise ValueError(
                "Currency code must contain exactly 3 letters"
            )

        if len(self.numeric_code) != 3 or not self.numeric_code.isdigit():
            raise ValueError(
                "Currency numeric code must contain exactly 3 digits"
            )

        if not self.name.strip():
            raise ValueError(
                "Currency name cannot be empty"
            )

        if not self.symbol.strip():
            raise ValueError(
                "Currency symbol cannot be empty"
            )

        if self.minor_units < 0:
            raise ValueError(
                "Currency minor units cannot be negative"
            )