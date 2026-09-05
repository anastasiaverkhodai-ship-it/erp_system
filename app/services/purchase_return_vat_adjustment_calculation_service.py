from dataclasses import dataclass
from datetime import date
from decimal import Decimal


ZERO = Decimal("0")

PURCHASE_RETURN_VAT_ADJUSTMENT_BASIS_KINDS = frozenset(
    {
        "goods_received_by_supplier",
        "refund_by_supplier",
    }
)


class PurchaseReturnVatAdjustmentCalculationError(
    Exception
):
    """Base Purchase Return VAT adjustment calculation error."""


class PurchaseReturnVatAdjustmentDataIntegrityError(
    PurchaseReturnVatAdjustmentCalculationError
):
    """VAT adjustment input is internally inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnVatAdjustmentTarget:
    """
    Complete desired VAT-adjustment state for one immutable source.

    Source identity:
        PurchaseReturnRecognitionEvent
        +
        TaxCalculation
        +
        basis_kind

    adjusted_taxable_base and adjusted_tax_amount are explicit,
    independent VAT amounts.

    This calculation layer deliberately does NOT derive either value from:
        PurchaseReturnRecognitionEvent.returned_base_amount
        returned_gross_amount - returned_tax_amount

    A zero target is valid reconciliation state. It means that an active
    original event, if present, must be immutably reversed. A zero target
    must never be persisted as a new original event.
    """

    purchase_return_recognition_event_id: int
    tax_calculation_id: int
    adjustment_date: date
    basis_kind: str
    adjusted_taxable_base: Decimal
    adjusted_tax_amount: Decimal
    currency_code: str

    @property
    def is_zero(
        self,
    ) -> bool:
        return (
            self.adjusted_taxable_base
            == ZERO
            and self.adjusted_tax_amount
            == ZERO
        )


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(value)
        )
    except Exception as exc:
        raise (
            PurchaseReturnVatAdjustmentDataIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseReturnVatAdjustmentDataIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


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
            PurchaseReturnVatAdjustmentDataIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _business_date(
    value,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise (
            PurchaseReturnVatAdjustmentDataIntegrityError(
                "adjustment_date must be a date"
            )
        )

    return value


def _basis_kind(
    value,
) -> str:
    if (
        not isinstance(
            value,
            str,
        )
        or value
        not in PURCHASE_RETURN_VAT_ADJUSTMENT_BASIS_KINDS
    ):
        raise (
            PurchaseReturnVatAdjustmentDataIntegrityError(
                "basis_kind must be one of: "
                "goods_received_by_supplier, "
                "refund_by_supplier"
            )
        )

    return value


def _currency_code(
    value,
) -> str:
    if (
        not isinstance(
            value,
            str,
        )
        or len(
            value
        )
        != 3
    ):
        raise (
            PurchaseReturnVatAdjustmentDataIntegrityError(
                "currency_code must contain exactly 3 characters"
            )
        )

    return value


def build_purchase_return_vat_adjustment_target(
    *,
    purchase_return_recognition_event_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    basis_kind: str,
    adjusted_taxable_base: Decimal,
    adjusted_tax_amount: Decimal,
    currency_code: str,
) -> PurchaseReturnVatAdjustmentTarget:
    """
    Build one normalized desired VAT-adjustment target.

    Monetary values are supplied independently by the caller.
    This foundation does not decide legal VAT slicing from PRRE /
    TaxCalculation. That belongs to the later reconciliation layer.

    Both amounts may be zero only to represent desired zero state.
    Negative values are never valid.
    """

    purchase_return_recognition_event_id = (
        _positive_id(
            purchase_return_recognition_event_id,
            field=(
                "purchase_return_recognition_event_id"
            ),
        )
    )

    tax_calculation_id = _positive_id(
        tax_calculation_id,
        field="tax_calculation_id",
    )

    adjustment_date = _business_date(
        adjustment_date
    )

    basis_kind = _basis_kind(
        basis_kind
    )

    adjusted_taxable_base = _decimal(
        adjusted_taxable_base,
        field="adjusted_taxable_base",
    )

    adjusted_tax_amount = _decimal(
        adjusted_tax_amount,
        field="adjusted_tax_amount",
    )

    if adjusted_taxable_base < ZERO:
        raise (
            PurchaseReturnVatAdjustmentDataIntegrityError(
                "adjusted_taxable_base cannot be negative"
            )
        )

    if adjusted_tax_amount < ZERO:
        raise (
            PurchaseReturnVatAdjustmentDataIntegrityError(
                "adjusted_tax_amount cannot be negative"
            )
        )

    currency_code = _currency_code(
        currency_code
    )

    return PurchaseReturnVatAdjustmentTarget(
        purchase_return_recognition_event_id=(
            purchase_return_recognition_event_id
        ),
        tax_calculation_id=(
            tax_calculation_id
        ),
        adjustment_date=(
            adjustment_date
        ),
        basis_kind=(
            basis_kind
        ),
        adjusted_taxable_base=(
            adjusted_taxable_base
        ),
        adjusted_tax_amount=(
            adjusted_tax_amount
        ),
        currency_code=(
            currency_code
        ),
    )
