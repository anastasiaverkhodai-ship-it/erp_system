from enum import StrEnum


class ChartOfAccountsTemplateType(StrEnum):
    """
    Supported Ukrainian commercial Chart of Accounts
    templates.

    GENERAL_291:
        General Chart of Accounts under Ministry of
        Finance Order No. 291.

    SIMPLIFIED_186:
        Simplified Chart of Accounts under Ministry
        of Finance Order No. 186.
    """

    GENERAL_291 = "general_291"
    SIMPLIFIED_186 = "simplified_186"
