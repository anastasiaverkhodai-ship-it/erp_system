from enum import StrEnum


class DocumentRelationType(StrEnum):
    """
    High-level relationship between ERP documents.

    BASED_ON
        Target document was created based on the source document.

    FULFILLS
        Target document fulfills all or part of the source document.

    SETTLES
        Target document settles or pays the source document.

    ADJUSTS
        Target document adjusts or corrects the source document.

    CANCELS
        Target document cancels or replaces the effect
        of the source document.
    """

    BASED_ON = "based_on"
    FULFILLS = "fulfills"
    SETTLES = "settles"
    ADJUSTS = "adjusts"
    CANCELS = "cancels"