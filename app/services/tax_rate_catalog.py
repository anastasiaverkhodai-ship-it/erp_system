from datetime import date

from app.services.tax_rate_definition import (
    TaxRateDefinition,
)


class TaxRateCatalogError(Exception):
    """Base error for tax rate catalog operations."""


class TaxRateNotFoundError(
    TaxRateCatalogError
):
    """Raised when no applicable tax rate exists."""


class DuplicateTaxRateError(
    TaxRateCatalogError
):
    """Raised when the same rate code/date is registered twice."""


class TaxRateTypeMismatchError(
    TaxRateCatalogError
):
    """
    Raised when versions of the same rate code
    use different tax types.
    """


class TaxRateCatalog:
    def __init__(
        self,
        rates: tuple[
            TaxRateDefinition,
            ...,
        ],
    ) -> None:
        self._rates: dict[
            tuple[str, date],
            TaxRateDefinition,
        ] = {}

        tax_types_by_code = {}

        for rate in rates:
            key = (
                rate.code,
                rate.effective_from,
            )

            if key in self._rates:
                raise DuplicateTaxRateError(
                    "Duplicate tax rate: "
                    f"code='{rate.code}', "
                    f"effective_from={rate.effective_from}"
                )

            existing_tax_type = (
                tax_types_by_code.get(rate.code)
            )

            if (
                existing_tax_type is not None
                and existing_tax_type != rate.tax_type
            ):
                raise TaxRateTypeMismatchError(
                    "Tax rate versions use different "
                    f"tax types: code='{rate.code}'"
                )

            tax_types_by_code[
                rate.code
            ] = rate.tax_type

            self._rates[key] = rate

    def get_exact(
        self,
        code: str,
        effective_from: date,
    ) -> TaxRateDefinition:
        rate = self._rates.get(
            (
                code,
                effective_from,
            )
        )

        if rate is None:
            raise TaxRateNotFoundError(
                "Tax rate is not registered: "
                f"code='{code}', "
                f"effective_from={effective_from}"
            )

        return rate

    def get_effective(
        self,
        code: str,
        effective_date: date,
    ) -> TaxRateDefinition:
        candidates = (
            rate
            for rate in self._rates.values()
            if (
                rate.code == code
                and rate.effective_from
                <= effective_date
            )
        )

        selected = max(
            candidates,
            key=lambda rate: rate.effective_from,
            default=None,
        )

        if selected is None:
            raise TaxRateNotFoundError(
                "No tax rate effective on or before "
                f"{effective_date}: code='{code}'"
            )

        return selected

    def all(
        self,
    ) -> tuple[TaxRateDefinition, ...]:
        return tuple(self._rates.values())