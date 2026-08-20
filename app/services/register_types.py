from enum import StrEnum


class RegisterKind(StrEnum):
    """
    High-level ERP register categories.

    ACCUMULATION
        Stores quantitative or monetary movements and can be used
        to calculate balances and turnovers.

    INFORMATION
        Stores facts, settings, prices, rates, parameters,
        classifications, and other effective-dated information.

    ACCOUNTING
        Stores double-entry accounting movements.
    """

    ACCUMULATION = "accumulation"
    INFORMATION = "information"
    ACCOUNTING = "accounting"