from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from app.services.currency_catalog_service import (
    SYSTEM_CURRENCY_CATALOG,
)


def round_currency_amount(
    amount: Decimal,
    currency_code: str,
) -> Decimal:
    """
    Round a monetary amount according to the currency's
    configured number of minor units.

    Example:

        UAH minor_units = 2

        125.456 -> 125.46
        125.454 -> 125.45
    """

    currency = SYSTEM_CURRENCY_CATALOG.get(
        currency_code
    )

    quantum = Decimal("1").scaleb(
        -currency.minor_units
    )

    return amount.quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )