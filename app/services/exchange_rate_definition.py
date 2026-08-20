from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.currency_definition import CurrencyDefinition


@dataclass(frozen=True, slots=True)
class ExchangeRateDefinition:
    """
    Immutable exchange rate effective on a specific date.

    rate means:

        quote_amount = base_amount * rate

    Example:

        base_currency = EUR
        quote_currency = UAH
        rate = 50.25

        100 EUR -> 5025 UAH
    """

    base_currency: CurrencyDefinition
    quote_currency: CurrencyDefinition
    effective_date: date
    rate: Decimal

    def __post_init__(self) -> None:
        if self.base_currency.code == self.quote_currency.code:
            raise ValueError(
                "Base and quote currencies must be different"
            )

        if self.rate <= 0:
            raise ValueError(
                "Exchange rate must be greater than zero"
            )

    @property
    def base_code(self) -> str:
        return self.base_currency.code

    @property
    def quote_code(self) -> str:
        return self.quote_currency.code