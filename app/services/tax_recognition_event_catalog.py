from decimal import Decimal

from app.services.tax_recognition_event_definition import (
    TaxRecognitionEventDefinition,
)


class TaxRecognitionEventCatalog:
    def __init__(
        self,
        events: tuple[
            TaxRecognitionEventDefinition,
            ...,
        ],
    ) -> None:
        self._events = tuple(events)

    def for_tax_source(
        self,
        company_id: int,
        tax_source_document_id: int,
        tax_source_document_line_id: int | None = None,
    ) -> tuple[TaxRecognitionEventDefinition, ...]:
        return tuple(
            event
            for event in self._events
            if (
                event.company_id == company_id
                and event.tax_source_document_id
                == tax_source_document_id
                and (
                    tax_source_document_line_id is None
                    or event.tax_source_document_line_id
                    == tax_source_document_line_id
                )
            )
        )

    def recognized_taxable_base(
        self,
        company_id: int,
        tax_source_document_id: int,
        tax_source_document_line_id: int | None = None,
    ) -> Decimal:
        return sum(
            (
                event.recognized_taxable_base
                for event in self.for_tax_source(
                    company_id=company_id,
                    tax_source_document_id=tax_source_document_id,
                    tax_source_document_line_id=(
                        tax_source_document_line_id
                    ),
                )
            ),
            start=Decimal("0"),
        )

    def recognized_tax_amount(
        self,
        company_id: int,
        tax_source_document_id: int,
        tax_source_document_line_id: int | None = None,
    ) -> Decimal:
        return sum(
            (
                event.recognized_tax_amount
                for event in self.for_tax_source(
                    company_id=company_id,
                    tax_source_document_id=tax_source_document_id,
                    tax_source_document_line_id=(
                        tax_source_document_line_id
                    ),
                )
            ),
            start=Decimal("0"),
        )

    def all(
        self,
    ) -> tuple[TaxRecognitionEventDefinition, ...]:
        return self._events