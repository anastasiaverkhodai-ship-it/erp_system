from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
PRICE_QUANTUM = Decimal("0.0001")


class SalesDiscountError(ValueError):
    """Invalid sales line discount snapshot."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesDiscountSnapshot:
    unit_price: Decimal
    base_unit_price: Decimal | None
    discount_percent: Decimal | None
    discount_amount_per_unit: Decimal | None


def _as_decimal(
    value: Decimal | int | str,
) -> Decimal:
    return Decimal(str(value))


def _quantize_price(
    value: Decimal | int | str,
) -> Decimal:
    return _as_decimal(value).quantize(
        PRICE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def calculate_sales_discount_snapshot(
    *,
    base_price: Decimal,
    discount_percent: Decimal | None = None,
    discount_amount_per_unit: Decimal | None = None,
) -> SalesDiscountSnapshot:
    base = _quantize_price(
        base_price
    )

    if base < ZERO:
        raise SalesDiscountError(
            "Base unit price cannot be negative"
        )

    if (
        discount_percent is not None
        and discount_amount_per_unit is not None
    ):
        raise SalesDiscountError(
            "Provide at most one discount mode"
        )

    if (
        discount_percent is None
        and discount_amount_per_unit is None
    ):
        return SalesDiscountSnapshot(
            unit_price=base,
            base_unit_price=None,
            discount_percent=None,
            discount_amount_per_unit=None,
        )

    if discount_percent is not None:
        percent = _as_decimal(
            discount_percent
        )

        if (
            percent < ZERO
            or percent > ONE_HUNDRED
        ):
            raise SalesDiscountError(
                "Discount percent must be "
                "between 0 and 100"
            )

        final_price = _quantize_price(
            base
            * (
                Decimal("1")
                - (
                    percent
                    / ONE_HUNDRED
                )
            )
        )

        return SalesDiscountSnapshot(
            unit_price=final_price,
            base_unit_price=base,
            discount_percent=percent,
            discount_amount_per_unit=None,
        )

    amount = _quantize_price(
        discount_amount_per_unit
    )

    if amount < ZERO:
        raise SalesDiscountError(
            "Discount amount per unit "
            "cannot be negative"
        )

    if amount > base:
        raise SalesDiscountError(
            "Discount amount per unit "
            "cannot exceed base unit price"
        )

    return SalesDiscountSnapshot(
        unit_price=_quantize_price(
            base - amount
        ),
        base_unit_price=base,
        discount_percent=None,
        discount_amount_per_unit=amount,
    )
