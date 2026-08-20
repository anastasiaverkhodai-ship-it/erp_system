from enum import StrEnum


class TaxType(StrEnum):
    """
    High-level tax types supported by the tax engine.

    VAT
        Value Added Tax.
    """

    VAT = "vat"


class TaxDirection(StrEnum):
    """
    Direction of a tax amount.

    INPUT
        Tax associated with purchases and potential
        input tax credit.

    OUTPUT
        Tax associated with sales and potential
        tax liability.
    """

    INPUT = "input"
    OUTPUT = "output"