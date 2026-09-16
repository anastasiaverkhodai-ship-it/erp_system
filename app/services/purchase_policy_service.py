"""Purchase supplier eligibility and document payment-term snapshots."""


class PurchasePolicyError(ValueError):
    pass


def validate_purchase_supplier(counterparty):
    if counterparty.counterparty_type not in {"supplier", "both"}:
        raise PurchasePolicyError("Purchase documents require a supplier or both-type counterparty")


def purchase_payment_terms(*, counterparty, contract, explicit_days):
    """Explicit zero means immediate payment; omission inherits agreed defaults."""
    days = explicit_days if explicit_days is not None else (
        contract.payment_term_days if contract is not None else counterparty.payment_term_days)
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        raise PurchasePolicyError("Purchase payment terms must be nonnegative whole days")
    return days
