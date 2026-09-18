from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException, status


MONEY_Q2 = Decimal("0.01")
ZERO = Decimal("0.00")


def _money(value: Decimal) -> Decimal:
    if not Decimal(value).is_finite():
        raise HTTPException(status_code=409, detail="Non-finite VAT amount")
    return Decimal(value).quantize(
        MONEY_Q2,
        rounding=ROUND_HALF_UP,
    )


@dataclass(frozen=True)
class OpeningCarryTranche:
    source_declaration_id: int
    origin_reporting_year: int
    origin_reporting_month: int
    opening_amount: Decimal


@dataclass(frozen=True)
class AppliedCarryTranche:
    source_declaration_id: int
    origin_reporting_year: int
    origin_reporting_month: int
    opening_amount: Decimal
    consumed_amount: Decimal
    closing_amount: Decimal


@dataclass(frozen=True)
class VatCarryResult:
    opening_negative_carry: Decimal
    vat_payable: Decimal
    current_period_negative: Decimal
    closing_negative_carry: Decimal
    tranches: tuple[AppliedCarryTranche, ...]


def apply_fifo_negative_carry(
    *,
    output_vat: Decimal,
    input_vat_credit: Decimal,
    opening_tranches: list[OpeningCarryTranche],
) -> VatCarryResult:
    output_vat = _money(output_vat)
    input_vat_credit = _money(input_vat_credit)

    ordered = sorted(
        opening_tranches,
        key=lambda item: (
            item.origin_reporting_year,
            item.origin_reporting_month,
            item.source_declaration_id,
        ),
    )

    seen_origins: set[tuple[int, int]] = set()

    for tranche in ordered:
        origin = (
            tranche.origin_reporting_year,
            tranche.origin_reporting_month,
        )

        if origin in seen_origins:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate VAT negative-carry origin tranche",
            )

        seen_origins.add(origin)

        if tranche.opening_amount < ZERO:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Negative opening carry tranche",
            )

    opening = _money(
        sum(
            (item.opening_amount for item in ordered),
            ZERO,
        )
    )

    net_before_carry = _money(
        output_vat - input_vat_credit
    )

    remaining_positive = (
        net_before_carry
        if net_before_carry > ZERO
        else ZERO
    )

    applied: list[AppliedCarryTranche] = []

    for tranche in ordered:
        opening_amount = _money(tranche.opening_amount)

        consumed = (
            min(opening_amount, remaining_positive)
            if remaining_positive > ZERO
            else ZERO
        )
        consumed = _money(consumed)

        closing = _money(
            opening_amount - consumed
        )

        remaining_positive = _money(
            remaining_positive - consumed
        )

        applied.append(
            AppliedCarryTranche(
                source_declaration_id=tranche.source_declaration_id,
                origin_reporting_year=tranche.origin_reporting_year,
                origin_reporting_month=tranche.origin_reporting_month,
                opening_amount=opening_amount,
                consumed_amount=consumed,
                closing_amount=closing,
            )
        )

    if net_before_carry > ZERO:
        vat_payable = _money(remaining_positive)
        current_negative = ZERO
    elif net_before_carry < ZERO:
        vat_payable = ZERO
        current_negative = _money(-net_before_carry)
    else:
        vat_payable = ZERO
        current_negative = ZERO

    prior_closing = _money(
        sum(
            (item.closing_amount for item in applied),
            ZERO,
        )
    )

    closing = _money(
        prior_closing + current_negative
    )

    return VatCarryResult(
        opening_negative_carry=opening,
        vat_payable=vat_payable,
        current_period_negative=current_negative,
        closing_negative_carry=closing,
        tranches=tuple(applied),
    )
