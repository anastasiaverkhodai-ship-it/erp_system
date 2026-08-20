from app.services.currency_catalog import SYSTEM_CURRENCIES
from app.services.currency_definition import CurrencyDefinition


class CurrencyCatalogError(Exception):
    """Base error for currency catalog operations."""


class CurrencyNotFoundError(
    CurrencyCatalogError
):
    """Raised when a currency code is not registered."""


class DuplicateCurrencyCodeError(
    CurrencyCatalogError
):
    """Raised when the same alphabetic currency code is registered twice."""


class DuplicateCurrencyNumericCodeError(
    CurrencyCatalogError
):
    """Raised when the same numeric currency code is registered twice."""


class CurrencyCatalog:
    def __init__(
        self,
        currencies: tuple[CurrencyDefinition, ...],
    ) -> None:
        self._by_code: dict[str, CurrencyDefinition] = {}
        self._by_numeric_code: dict[str, CurrencyDefinition] = {}

        for currency in currencies:
            if currency.code in self._by_code:
                raise DuplicateCurrencyCodeError(
                    f"Duplicate currency code: '{currency.code}'"
                )

            if currency.numeric_code in self._by_numeric_code:
                raise DuplicateCurrencyNumericCodeError(
                    "Duplicate currency numeric code: "
                    f"'{currency.numeric_code}'"
                )

            self._by_code[currency.code] = currency
            self._by_numeric_code[currency.numeric_code] = currency

    def get(
        self,
        code: str,
    ) -> CurrencyDefinition:
        currency = self._by_code.get(code)

        if currency is None:
            raise CurrencyNotFoundError(
                f"Currency '{code}' is not registered"
            )

        return currency

    def get_by_numeric_code(
        self,
        numeric_code: str,
    ) -> CurrencyDefinition:
        currency = self._by_numeric_code.get(
            numeric_code
        )

        if currency is None:
            raise CurrencyNotFoundError(
                "Currency numeric code "
                f"'{numeric_code}' is not registered"
            )

        return currency

    def all(
        self,
    ) -> tuple[CurrencyDefinition, ...]:
        return tuple(self._by_code.values())


SYSTEM_CURRENCY_CATALOG = CurrencyCatalog(
    currencies=SYSTEM_CURRENCIES,
)