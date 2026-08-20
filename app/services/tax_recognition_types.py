from enum import StrEnum


class TaxRecognitionMethod(StrEnum):
    """
    Method used to determine when calculated tax
    becomes recognized.

    FIRST_EVENT
        Recognition is driven by the first relevant
        tax recognition event.

    CASH_METHOD
        Recognition is driven by payment events and
        may occur partially as payments are received
        or made.

    MANUAL
        Recognition is explicitly controlled by an
        authorized business process.
    """

    FIRST_EVENT = "first_event"
    CASH_METHOD = "cash_method"
    MANUAL = "manual"


class TaxRecognitionStatus(StrEnum):
    """
    Recognition state of a calculated tax amount.
    """

    PENDING = "pending"
    PARTIALLY_RECOGNIZED = "partially_recognized"
    RECOGNIZED = "recognized"