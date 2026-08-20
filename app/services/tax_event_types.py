from enum import StrEnum


class TaxEventType(StrEnum):
    """
    High-level tax events used by the ERP tax engine.

    TAXABLE_SUPPLY
        Taxable sale or other taxable supply.

    TAXABLE_PURCHASE
        Taxable purchase that may create input tax.

    PAYMENT_RECEIVED
        Customer payment received.

    PAYMENT_MADE
        Payment made to a supplier.

    ADJUSTMENT
        Tax adjustment or correction.

    REVERSAL
        Reversal of a previously recognized tax event.
    """

    TAXABLE_SUPPLY = "taxable_supply"
    TAXABLE_PURCHASE = "taxable_purchase"
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_MADE = "payment_made"
    ADJUSTMENT = "adjustment"
    REVERSAL = "reversal"