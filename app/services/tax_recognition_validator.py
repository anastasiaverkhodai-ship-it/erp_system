from app.services.tax_calculation_result import (
    TaxCalculationResult,
)
from app.services.tax_recognition_event_catalog import (
    TaxRecognitionEventCatalog,
)


class TaxRecognitionValidationError(Exception):
    """Base error for tax recognition validation."""


class TaxableBaseOverRecognitionError(
    TaxRecognitionValidationError
):
    """Raised when recognized taxable base exceeds calculated base."""


class TaxAmountOverRecognitionError(
    TaxRecognitionValidationError
):
    """Raised when recognized tax exceeds calculated tax amount."""


class TaxRecognitionCurrencyMismatchError(
    TaxRecognitionValidationError
):
    """Raised when recognition event currency differs from tax currency."""


def validate_tax_recognition(
    company_id: int,
    tax_source_document_id: int,
    calculation: TaxCalculationResult,
    catalog: TaxRecognitionEventCatalog,
    tax_source_document_line_id: int | None = None,
) -> None:
    """
    Validate accumulated recognition events against
    the original tax calculation.
    """

    if company_id <= 0:
        raise ValueError(
            "Company ID must be greater than zero"
        )

    if tax_source_document_id <= 0:
        raise ValueError(
            "Tax source document ID must be greater than zero"
        )

    if (
        tax_source_document_line_id is not None
        and tax_source_document_line_id <= 0
    ):
        raise ValueError(
            "Tax source document line ID "
            "must be greater than zero"
        )

    events = catalog.for_tax_source(
        company_id=company_id,
        tax_source_document_id=tax_source_document_id,
        tax_source_document_line_id=(
            tax_source_document_line_id
        ),
    )

    for event in events:
        if event.currency_code != calculation.currency_code:
            raise TaxRecognitionCurrencyMismatchError(
                "Tax recognition currency does not match "
                "calculation currency: "
                f"expected='{calculation.currency_code}', "
                f"actual='{event.currency_code}'"
            )

    recognized_base = catalog.recognized_taxable_base(
        company_id=company_id,
        tax_source_document_id=tax_source_document_id,
        tax_source_document_line_id=(
            tax_source_document_line_id
        ),
    )

    recognized_tax = catalog.recognized_tax_amount(
        company_id=company_id,
        tax_source_document_id=tax_source_document_id,
        tax_source_document_line_id=(
            tax_source_document_line_id
        ),
    )

    if recognized_base > calculation.taxable_base:
        raise TaxableBaseOverRecognitionError(
            "Recognized taxable base exceeds "
            "calculated taxable base: "
            f"calculated={calculation.taxable_base}, "
            f"recognized={recognized_base}"
        )

    if recognized_tax > calculation.tax_amount:
        raise TaxAmountOverRecognitionError(
            "Recognized tax amount exceeds "
            "calculated tax amount: "
            f"calculated={calculation.tax_amount}, "
            f"recognized={recognized_tax}"
        )