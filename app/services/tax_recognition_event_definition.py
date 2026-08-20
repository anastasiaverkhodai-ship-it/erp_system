from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class TaxRecognitionEventDefinition:
    """
    Immutable event that recognizes part of a calculated tax.

    company_id
        Company that owns the tax recognition event.

    tax_source_document_id
        Original taxable document whose tax is being recognized.

    recognition_source_document_id
        Document that caused recognition, for example a payment.

    recognition_date
        Date on which tax is recognized.

    recognized_taxable_base
        Part of the taxable base recognized by this event.

    recognized_tax_amount
        Part of the calculated tax recognized by this event.

    currency_code
        Currency of the recognized amounts.

    tax_source_document_line_id
        Optional line of the original taxable document.

    recognition_source_document_line_id
        Optional line of the recognition document.
    """

    company_id: int
    tax_source_document_id: int
    recognition_source_document_id: int
    recognition_date: date
    recognized_taxable_base: Decimal
    recognized_tax_amount: Decimal
    currency_code: str
    tax_source_document_line_id: int | None = None
    recognition_source_document_line_id: int | None = None

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "Company ID must be greater than zero"
            )

        if self.tax_source_document_id <= 0:
            raise ValueError(
                "Tax source document ID must be greater than zero"
            )

        if self.recognition_source_document_id <= 0:
            raise ValueError(
                "Recognition source document ID "
                "must be greater than zero"
            )

        if (
            self.tax_source_document_line_id is not None
            and self.tax_source_document_line_id <= 0
        ):
            raise ValueError(
                "Tax source document line ID "
                "must be greater than zero"
            )

        if (
            self.recognition_source_document_line_id is not None
            and self.recognition_source_document_line_id <= 0
        ):
            raise ValueError(
                "Recognition source document line ID "
                "must be greater than zero"
            )

        if self.recognized_taxable_base < 0:
            raise ValueError(
                "Recognized taxable base cannot be negative"
            )

        if self.recognized_tax_amount < 0:
            raise ValueError(
                "Recognized tax amount cannot be negative"
            )

        if (
            len(self.currency_code) != 3
            or not self.currency_code.isalpha()
            or self.currency_code != self.currency_code.upper()
        ):
            raise ValueError(
                "Currency code must contain exactly "
                "3 uppercase letters"
            )