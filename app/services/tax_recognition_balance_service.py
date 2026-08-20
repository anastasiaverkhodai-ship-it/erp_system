from app.services.tax_calculation_result import (
    TaxCalculationResult,
)
from app.services.tax_recognition_balance import (
    TaxRecognitionBalance,
)
from app.services.tax_recognition_event_catalog import (
    TaxRecognitionEventCatalog,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_recognition_validator import (
    validate_tax_recognition,
)


def build_tax_recognition_balance(
    company_id: int,
    tax_source_document_id: int,
    calculation: TaxCalculationResult,
    method: TaxRecognitionMethod,
    catalog: TaxRecognitionEventCatalog,
    tax_source_document_line_id: int | None = None,
) -> TaxRecognitionBalance:
    """
    Build the aggregated recognition balance for
    a tax calculation from recognition events.
    """

    validate_tax_recognition(
        company_id=company_id,
        tax_source_document_id=tax_source_document_id,
        calculation=calculation,
        catalog=catalog,
        tax_source_document_line_id=(
            tax_source_document_line_id
        ),
    )

    recognized_taxable_base = (
        catalog.recognized_taxable_base(
            company_id=company_id,
            tax_source_document_id=tax_source_document_id,
            tax_source_document_line_id=(
                tax_source_document_line_id
            ),
        )
    )

    recognized_tax_amount = (
        catalog.recognized_tax_amount(
            company_id=company_id,
            tax_source_document_id=tax_source_document_id,
            tax_source_document_line_id=(
                tax_source_document_line_id
            ),
        )
    )

    return TaxRecognitionBalance(
        calculation=calculation,
        method=method,
        recognized_taxable_base=recognized_taxable_base,
        recognized_tax_amount=recognized_tax_amount,
    )