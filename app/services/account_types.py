from enum import StrEnum


class AccountType(StrEnum):
    """
    Financial/accounting classification of an account.

    This is intentionally separate from normal balance.
    """

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"
    OFF_BALANCE = "off_balance"


class AccountNormalBalance(StrEnum):
    """
    Normal balance behavior of an accounting account.
    """

    DEBIT = "debit"
    CREDIT = "credit"
    DEBIT_CREDIT = "debit_credit"
