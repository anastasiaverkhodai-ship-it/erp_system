from enum import StrEnum


class TaxTreatment(StrEnum):
    """
    Tax treatment of a transaction.

    TAXABLE
        Transaction is subject to tax at a positive rate.

    ZERO_RATED
        Transaction is taxable, but the applicable tax rate is 0%.

    EXEMPT
        Transaction is exempt from tax.

    OUT_OF_SCOPE
        Transaction is outside the scope of the tax.
    """

    TAXABLE = "taxable"
    ZERO_RATED = "zero_rated"
    EXEMPT = "exempt"
    OUT_OF_SCOPE = "out_of_scope"