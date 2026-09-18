from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException, status

from app.services.vat_declaration_source_loader_service import (
    VatDeclarationSource,
)


MONEY_Q2 = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(
        MONEY_Q2,
        rounding=ROUND_HALF_UP,
    )


@dataclass(frozen=True)
class VatDeclarationPeriodTotals:
    output_taxable_base: Decimal
    output_vat: Decimal
    input_taxable_base: Decimal
    input_vat_credit: Decimal


def aggregate_vat_declaration_sources(
    sources: list[VatDeclarationSource],
) -> VatDeclarationPeriodTotals:
    output_base = ZERO
    output_vat = ZERO
    input_base = ZERO
    input_vat = ZERO

    seen=set()
    for source in sources:
        identity=(source.source_kind,source.source_id)
        if identity in seen or not all(Decimal(v).is_finite() for v in (source.taxable_base_delta,source.tax_amount_delta)):
            raise HTTPException(status_code=409, detail="Duplicate or non-finite declaration source")
        seen.add(identity)
        if source.currency_code != "UAH":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="VAT declaration V1 supports UAH only",
            )

        if source.direction == "output":
            output_base += source.taxable_base_delta
            output_vat += source.tax_amount_delta
        elif source.direction == "input":
            input_base += source.taxable_base_delta
            input_vat += source.tax_amount_delta
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Unsupported VAT declaration direction",
            )

    output_base = money(output_base)
    output_vat = money(output_vat)
    input_base = money(input_base)
    input_vat = money(input_vat)

    return VatDeclarationPeriodTotals(
        output_taxable_base=output_base,
        output_vat=output_vat,
        input_taxable_base=input_base,
        input_vat_credit=input_vat,
    )
