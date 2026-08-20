from app.services.currency_definition import CurrencyDefinition


UAH = CurrencyDefinition(
    code="UAH",
    numeric_code="980",
    name="Ukrainian Hryvnia",
    symbol="₴",
    minor_units=2,
)

EUR = CurrencyDefinition(
    code="EUR",
    numeric_code="978",
    name="Euro",
    symbol="€",
    minor_units=2,
)

USD = CurrencyDefinition(
    code="USD",
    numeric_code="840",
    name="US Dollar",
    symbol="$",
    minor_units=2,
)


SYSTEM_CURRENCIES: tuple[CurrencyDefinition, ...] = (
    UAH,
    EUR,
    USD,
)