from datetime import date
from decimal import Decimal

from app.services.exchange_rate_definition import (
    ExchangeRateDefinition,
)


class ExchangeRateCatalogError(Exception):
    """Base error for exchange rate catalog operations."""


class ExchangeRateNotFoundError(
    ExchangeRateCatalogError
):
    """Raised when an exchange rate is not registered."""


class DuplicateExchangeRateError(
    ExchangeRateCatalogError
):
    """Raised when the same currency pair/date is registered twice."""


class ExchangeRateCatalog:
    def __init__(
        self,
        rates: tuple[ExchangeRateDefinition, ...],
    ) -> None:
        self._rates: dict[
            tuple[str, str, date],
            ExchangeRateDefinition,
        ] = {}

        for rate in rates:
            direct_key = (
                rate.base_code,
                rate.quote_code,
                rate.effective_date,
            )

            reverse_key = (
                rate.quote_code,
                rate.base_code,
                rate.effective_date,
            )

            if (
                direct_key in self._rates
                or reverse_key in self._rates
            ):
                raise DuplicateExchangeRateError(
                    "Duplicate exchange rate: "
                    f"'{rate.base_code}' "
                    f"<-> '{rate.quote_code}' "
                    f"on {rate.effective_date}"
                )

            self._rates[direct_key] = rate

    def get_rate(
        self,
        base_code: str,
        quote_code: str,
        effective_date: date,
    ) -> Decimal:
        if base_code == quote_code:
            return Decimal("1")

        direct = self._rates.get(
            (
                base_code,
                quote_code,
                effective_date,
            )
        )

        if direct is not None:
            return direct.rate

        reverse = self._rates.get(
            (
                quote_code,
                base_code,
                effective_date,
            )
        )

        if reverse is not None:
            return Decimal("1") / reverse.rate

        raise ExchangeRateNotFoundError(
            "Exchange rate is not registered: "
            f"'{base_code}' -> '{quote_code}' "
            f"on {effective_date}"
        )

    def get_effective_rate(
        self,
        base_code: str,
        quote_code: str,
        effective_date: date,
    ) -> Decimal:
        if base_code == quote_code:
            return Decimal("1")

        candidates: list[
            tuple[
                date,
                ExchangeRateDefinition,
                bool,
            ]
        ] = []

        for rate in self._rates.values():
            if rate.effective_date > effective_date:
                continue

            if (
                rate.base_code == base_code
                and rate.quote_code == quote_code
            ):
                candidates.append(
                    (
                        rate.effective_date,
                        rate,
                        False,
                    )
                )

            elif (
                rate.base_code == quote_code
                and rate.quote_code == base_code
            ):
                candidates.append(
                    (
                        rate.effective_date,
                        rate,
                        True,
                    )
                )

        if not candidates:
            raise ExchangeRateNotFoundError(
                "No exchange rate effective on or before "
                f"{effective_date}: "
                f"'{base_code}' -> '{quote_code}'"
            )

        _, selected_rate, is_reverse = max(
            candidates,
            key=lambda item: item[0],
        )

        if is_reverse:
            return Decimal("1") / selected_rate.rate

        return selected_rate.rate

    def all(
        self,
    ) -> tuple[ExchangeRateDefinition, ...]:
        return tuple(self._rates.values())