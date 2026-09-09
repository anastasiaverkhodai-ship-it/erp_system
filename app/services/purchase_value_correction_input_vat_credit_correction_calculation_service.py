from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


MONEY = Decimal("0.01")
ZERO = Decimal("0.00")


class PurchaseValueCorrectionInputVatCreditCorrectionCalculationError(
    ValueError
):
    pass


@dataclass(frozen=True)
class PurchaseValueCorrectionInputVatCreditCorrectionTarget:
    purchase_value_correction_vat_adjustment_event_id: int
    tax_calculation_id: int
    tax_credit_evidence_id: int | None
    adjustment_date: date
    correction_kind: str
    corrected_taxable_base: Decimal
    corrected_tax_amount: Decimal
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
            PurchaseValueCorrectionInputVatCreditCorrectionCalculationError(
                f"{field} must be a decimal amount"
            )
        ) from exc

    if result < ZERO:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionCalculationError(
                f"{field} must be nonnegative"
            )
        )

    return result


def build_purchase_value_correction_input_vat_credit_target(
    *,
    purchase_value_correction_vat_adjustment_event_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    adjustment_kind: str,
    adjusted_taxable_base: Decimal,
    adjusted_tax_amount: Decimal,
    recognized_credit_taxable_base: Decimal,
    recognized_credit_tax_amount: Decimal,
    currency_code: str,
    tax_credit_evidence_id: int | None = None,
    tax_credit_evidence_type: str | None = None,
) -> PurchaseValueCorrectionInputVatCreditCorrectionTarget | None:
    """
    Pure legal-credit target.

    decrease:
        does not require RK evidence;
        cannot reduce more credit than was actually recognized.

    increase:
        requires REGISTERED_ADJUSTMENT evidence.

    Persistence and source validation remain separate.
    """

    if purchase_value_correction_vat_adjustment_event_id <= 0:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionCalculationError(
                "purchase_value_correction_vat_adjustment_event_id "
                "must be positive"
            )
        )

    if tax_calculation_id <= 0:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionCalculationError(
                "tax_calculation_id must be positive"
            )
        )

    if adjustment_kind not in {
        "decrease",
        "increase",
    }:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionCalculationError(
                "adjustment_kind must be decrease or increase"
            )
        )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionCalculationError(
                "adjustment_date must be a date"
            )
        )

    currency = str(
        currency_code
    ).strip().upper()

    if len(currency) != 3:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionCalculationError(
                "currency_code must contain exactly 3 characters"
            )
        )

    adjusted_base = _money(
        adjusted_taxable_base,
        field="adjusted_taxable_base",
    )
    adjusted_tax = _money(
        adjusted_tax_amount,
        field="adjusted_tax_amount",
    )

    recognized_base = _money(
        recognized_credit_taxable_base,
        field="recognized_credit_taxable_base",
    )
    recognized_tax = _money(
        recognized_credit_tax_amount,
        field="recognized_credit_tax_amount",
    )

    if (
        adjusted_base == ZERO
        and adjusted_tax == ZERO
    ):
        return None

    if adjustment_kind == "decrease":
        if tax_credit_evidence_id is not None:
            raise (
                PurchaseValueCorrectionInputVatCreditCorrectionCalculationError(
                    "decrease must not depend on RK evidence"
                )
            )

        corrected_base = min(
            adjusted_base,
            recognized_base,
        )
        corrected_tax = min(
            adjusted_tax,
            recognized_tax,
        )

        if (
            corrected_base == ZERO
            and corrected_tax == ZERO
        ):
            return None

        return (
            PurchaseValueCorrectionInputVatCreditCorrectionTarget(
                purchase_value_correction_vat_adjustment_event_id=(
                    purchase_value_correction_vat_adjustment_event_id
                ),
                tax_calculation_id=tax_calculation_id,
                tax_credit_evidence_id=None,
                adjustment_date=adjustment_date,
                correction_kind="decrease",
                corrected_taxable_base=corrected_base,
                corrected_tax_amount=corrected_tax,
                currency_code=currency,
            )
        )

    if (
        tax_credit_evidence_id is None
        or tax_credit_evidence_id <= 0
    ):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionCalculationError(
                "increase requires registered adjustment evidence"
            )
        )

    if (
        tax_credit_evidence_type
        != "registered_adjustment"
    ):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionCalculationError(
                "increase evidence must be REGISTERED_ADJUSTMENT"
            )
        )

    return PurchaseValueCorrectionInputVatCreditCorrectionTarget(
        purchase_value_correction_vat_adjustment_event_id=(
            purchase_value_correction_vat_adjustment_event_id
        ),
        tax_calculation_id=tax_calculation_id,
        tax_credit_evidence_id=tax_credit_evidence_id,
        adjustment_date=adjustment_date,
        correction_kind="increase",
        corrected_taxable_base=adjusted_base,
        corrected_tax_amount=adjusted_tax,
        currency_code=currency,
    )
