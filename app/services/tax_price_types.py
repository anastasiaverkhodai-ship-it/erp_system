from enum import StrEnum


class TaxPriceMode(StrEnum):
    """
    Commercial price interpretation for tax calculation.

    EXCLUSIVE
        Commercial line price does not include tax.
        Tax is calculated on top of the commercial amount.

    INCLUSIVE
        Commercial line price already includes tax.
        Taxable base and tax amount must be extracted
        from the tax-inclusive commercial amount.
    """

    EXCLUSIVE = "exclusive"
    INCLUSIVE = "inclusive"
