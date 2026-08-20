from datetime import date
from decimal import Decimal

from app.services.exchange_rate_catalog import (
    ExchangeRateCatalog,
)


def convert_currency_amount(
    amount: Decimal,
    source_code: str,
    target_code: str,
    effective_date: date,
    catalog: ExchangeRateCatalog,
    use_effective_rate: bool = True,
) -> Decimal:
    """
    Convert a monetary amount using an exchange rate.

    By default, the latest available rate effective on or
    before the requested date is used.

    If use_effective_rate is False, an exact-date rate
    is required.

    The function does not round the result.
    Monetary rounding belongs to a higher business layer.
    """

    if not source_code.strip():
        raise ValueError(
            "Source currency code cannot be empty"
        )

    if not target_code.strip():
        raise ValueError(
            "Target currency code cannot be empty"
        )

    if source_code == target_code:
        return amount

    if use_effective_rate:
        rate = catalog.get_effective_rate(
            base_code=source_code,
            quote_code=target_code,
            effective_date=effective_date,
        )
    else:
        rate = catalog.get_rate(
            base_code=source_code,
            quote_code=target_code,
            effective_date=effective_date,
        )

    return amount * rate