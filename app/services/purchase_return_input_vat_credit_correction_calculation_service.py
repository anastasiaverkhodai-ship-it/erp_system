from dataclasses import dataclass
from datetime import date
from decimal import Decimal


ZERO = Decimal("0")


class PurchaseReturnInputVatCreditCorrectionCalculationError(
    Exception
):
    """Legal INPUT VAT credit correction calculation is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnInputVatCreditCorrectionTarget:
    purchase_return_vat_adjustment_event_id: int
    tax_calculation_id: int
    adjustment_date: date
    reduced_taxable_base: Decimal
    reduced_tax_amount: Decimal
    currency_code: str

    @property
    def is_zero(
        self,
    ) -> bool:
        return (
            self.reduced_taxable_base == ZERO
            and self.reduced_tax_amount == ZERO
        )


def _positive_id(
    value,
    *,
    field: str,
) -> int:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value <= 0
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _amount(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(
                value
            )
        )
    except Exception as exc:
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                f"{field} must be finite"
            )
        )

    if result < ZERO:
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                f"{field} cannot be negative"
            )
        )

    return result


def _currency(
    value,
) -> str:
    if (
        not isinstance(
            value,
            str,
        )
        or len(
            value
        ) != 3
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                "currency_code must contain exactly 3 characters"
            )
        )

    return value


def _source_reduction(
    *,
    calculation_amount: Decimal,
    formed_credit_amount: Decimal,
    prior_return_amount: Decimal,
    current_return_amount: Decimal,
    field: str,
) -> Decimal:
    if formed_credit_amount > calculation_amount:
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                f"{field} formed credit cannot exceed "
                "TaxCalculation capacity"
            )
        )

    if prior_return_amount > calculation_amount:
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                f"{field} prior return capacity cannot exceed "
                "TaxCalculation capacity"
            )
        )

    cumulative_return = (
        prior_return_amount
        + current_return_amount
    )

    if cumulative_return > calculation_amount:
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                f"{field} cumulative return capacity cannot exceed "
                "TaxCalculation capacity"
            )
        )

    remaining_before = (
        calculation_amount
        - prior_return_amount
    )

    remaining_after = (
        calculation_amount
        - cumulative_return
    )

    correction_before = max(
        formed_credit_amount
        - remaining_before,
        ZERO,
    )

    correction_after = max(
        formed_credit_amount
        - remaining_after,
        ZERO,
    )

    result = (
        correction_after
        - correction_before
    )

    if result < ZERO:
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                f"{field} source correction became negative"
            )
        )

    if result > current_return_amount:
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                f"{field} source correction exceeds "
                "current return adjustment"
            )
        )

    return result


def build_purchase_return_input_vat_credit_correction_target(
    *,
    purchase_return_vat_adjustment_event_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    calculation_taxable_base,
    calculation_tax_amount,
    formed_credit_taxable_base,
    formed_credit_tax_amount,
    prior_active_return_taxable_base,
    prior_active_return_tax_amount,
    current_return_taxable_base,
    current_return_tax_amount,
    currency_code: str,
) -> PurchaseReturnInputVatCreditCorrectionTarget:
    """
    Calculate the legal INPUT VAT credit reduction attributable
    to one ACTIVE PurchaseReturnVatAdjustmentEvent.

    The caller supplies the amount of INPUT VAT credit that was
    actually formed as of adjustment_date.

    The current return does not automatically reduce all of its
    VAT amount. It reduces credit only to the extent that already
    formed credit exceeds the remaining TaxCalculation entitlement.

    For each monetary dimension independently:

        remaining_before =
            calculation
            - prior ACTIVE return adjustments

        remaining_after =
            remaining_before
            - current return adjustment

        correction_before =
            max(
                formed_credit
                - remaining_before,
                0
            )

        correction_after =
            max(
                formed_credit
                - remaining_after,
                0
            )

        current_source_correction =
            correction_after
            - correction_before

    Examples for tax amount:

        calculation 20
        formed 20
        return 5
        -> correction 5

        calculation 20
        formed 18
        return 5
        -> correction 3

        calculation 20
        formed 10
        return 5
        -> correction 0

        calculation 20
        formed 0
        return 5
        -> correction 0

    Zero-zero target is valid as a reconciliation desired state.
    It must not be persisted as an original DB row.
    """

    source_id = _positive_id(
        purchase_return_vat_adjustment_event_id,
        field=(
            "purchase_return_vat_adjustment_event_id"
        ),
    )

    calculation_id = _positive_id(
        tax_calculation_id,
        field="tax_calculation_id",
    )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                "adjustment_date must be a date"
            )
        )

    calculation_base = _amount(
        calculation_taxable_base,
        field="calculation_taxable_base",
    )

    calculation_tax = _amount(
        calculation_tax_amount,
        field="calculation_tax_amount",
    )

    formed_base = _amount(
        formed_credit_taxable_base,
        field="formed_credit_taxable_base",
    )

    formed_tax = _amount(
        formed_credit_tax_amount,
        field="formed_credit_tax_amount",
    )

    prior_base = _amount(
        prior_active_return_taxable_base,
        field="prior_active_return_taxable_base",
    )

    prior_tax = _amount(
        prior_active_return_tax_amount,
        field="prior_active_return_tax_amount",
    )

    current_base = _amount(
        current_return_taxable_base,
        field="current_return_taxable_base",
    )

    current_tax = _amount(
        current_return_tax_amount,
        field="current_return_tax_amount",
    )

    if (
        current_base == ZERO
        and current_tax == ZERO
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionCalculationError(
                "Current Purchase Return VAT adjustment "
                "must contain a nonzero base or tax amount"
            )
        )

    reduced_base = _source_reduction(
        calculation_amount=(
            calculation_base
        ),
        formed_credit_amount=(
            formed_base
        ),
        prior_return_amount=(
            prior_base
        ),
        current_return_amount=(
            current_base
        ),
        field="taxable base",
    )

    reduced_tax = _source_reduction(
        calculation_amount=(
            calculation_tax
        ),
        formed_credit_amount=(
            formed_tax
        ),
        prior_return_amount=(
            prior_tax
        ),
        current_return_amount=(
            current_tax
        ),
        field="tax amount",
    )

    return (
        PurchaseReturnInputVatCreditCorrectionTarget(
            purchase_return_vat_adjustment_event_id=(
                source_id
            ),
            tax_calculation_id=(
                calculation_id
            ),
            adjustment_date=(
                adjustment_date
            ),
            reduced_taxable_base=(
                reduced_base
            ),
            reduced_tax_amount=(
                reduced_tax
            ),
            currency_code=_currency(
                currency_code
            ),
        )
    )
