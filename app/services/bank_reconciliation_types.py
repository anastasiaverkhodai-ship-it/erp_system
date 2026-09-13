from enum import StrEnum


class BankReconciliationStatus(StrEnum):
    ACTIVE = "active"
    REVERSED = "reversed"
