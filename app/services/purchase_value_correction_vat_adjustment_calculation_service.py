from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


MONEY = Decimal("0.01")
ZERO = Decimal("0.00")


class PurchaseValueCorrectionVatAdjustmentCalculationError(
    ValueError
):
    pass


@dataclass(frozen=True)
class PurchaseValueCorrectionVatAdjustmentTarget:
    trade_value_correction_event_id: int
    tax_calculation_id: int
    adjustment_date: date
    adjustment_kind: str
    adjusted_taxable_base: Decimal
    adjusted_tax_amount: Decimal
    currency_code: str


def _money(
    value: Decimal,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(value).quantize(
            MONEY,
            rounding=ROUND_HALF_UP,
        )
    except Exception as exc:
        raise (
            PurchaseValueCorrectionVatAdjustmentCalculationError(
                f"{field} must be a decimal amount"
            )
        ) from exc

    if result < ZERO:
        raise (
            PurchaseValueCorrectionVatAdjustmentCalculationError(
                f"{field} must be nonnegative"
            )
        )

    return result


def build_purchase_value_correction_vat_adjustment_target(
    *,
    trade_value_correction_event_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    original_taxable_base: Decimal,
    corrected_taxable_base: Decimal,
    original_tax_amount: Decimal,
    corrected_tax_amount: Decimal,
    currency_code: str,
) -> PurchaseValueCorrectionVatAdjustmentTarget | None:
    """
    Build the immutable economic VAT delta for one purchase PVC.

    Amount fields on the target are absolute magnitudes.
    Direction is represented by adjustment_kind.

    Mixed-sign base/tax corrections fail closed because one immutable
    VAT correction source must have one economic direction.
    """

    if trade_value_correction_event_id <= 0:
        raise (
            PurchaseValueCorrectionVatAdjustmentCalculationError(
                "trade_value_correction_event_id must be positive"
            )
        )

    if tax_calculation_id <= 0:
        raise (
            PurchaseValueCorrectionVatAdjustmentCalculationError(
                "tax_calculation_id must be positive"
            )
        )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise (
            PurchaseValueCorrectionVatAdjustmentCalculationError(
                "adjustment_date must be a date"
            )
        )

    currency = str(
        currency_code
    ).strip().upper()

    if len(currency) != 3:
        raise (
            PurchaseValueCorrectionVatAdjustmentCalculationError(
                "currency_code must contain exactly 3 characters"
            )
        )

    original_base = _money(
        original_taxable_base,
        field="original_taxable_base",
    )
    corrected_base = _money(
        corrected_taxable_base,
        field="corrected_taxable_base",
    )
    original_tax = _money(
        original_tax_amount,
        field="original_tax_amount",
    )
    corrected_tax = _money(
        corrected_tax_amount,
        field="corrected_tax_amount",
    )

    base_delta = corrected_base - original_base
    tax_delta = corrected_tax - original_tax

    if (
        base_delta == ZERO
        and tax_delta == ZERO
    ):
        return None

    signs = {
        value.compare(ZERO)
        for value in (
            base_delta,
            tax_delta,
        )
        if value != ZERO
    }

    if len(signs) != 1:
        raise (
            PurchaseValueCorrectionVatAdjustmentCalculationError(
                "taxable-base and VAT corrections must have "
                "one economic direction"
            )
        )

    is_increase = next(
        iter(signs)
    ) > 0

    return PurchaseValueCorrectionVatAdjustmentTarget(
        trade_value_correction_event_id=(
            trade_value_correction_event_id
        ),
        tax_calculation_id=tax_calculation_id,
        adjustment_date=adjustment_date,
        adjustment_kind=(
            "increase"
            if is_increase
            else "decrease"
        ),
        adjusted_taxable_base=abs(
            base_delta
        ),
        adjusted_tax_amount=abs(
            tax_delta
        ),
        currency_code=currency,
    )
