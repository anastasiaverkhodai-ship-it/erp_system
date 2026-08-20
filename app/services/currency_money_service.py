from datetime import date
from decimal import Decimal

from app.services.currency_conversion_service import (
    convert_currency_amount,
)
from app.services.exchange_rate_catalog import (
    ExchangeRateCatalog,
)
from app.services.money_rounding import (
    round_currency_amount,
)


def convert_and_round_currency_amount(
    amount: Decimal,
    source_code: str,
    target_code: str,
    effective_date: date,
    catalog: ExchangeRateCatalog,
    use_effective_rate: bool = True,
) -> Decimal:
    """
    Convert a monetary amount and round the result
    according to the target currency's minor units.
    """

    converted_amount = convert_currency_amount(
        amount=amount,
        source_code=source_code,
        target_code=target_code,
        effective_date=effective_date,
        catalog=catalog,
        use_effective_rate=use_effective_rate,
    )

    return round_currency_amount(
        amount=converted_amount,
        currency_code=target_code,
    )