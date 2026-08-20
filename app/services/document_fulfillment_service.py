from decimal import Decimal

from app.services.document_fulfillment_types import (
    FulfillmentStatus,
)
from app.services.document_line_relation_catalog import (
    DocumentLineRelationCatalog,
)


class DocumentFulfillmentError(Exception):
    """Base error for document fulfillment calculations."""


class DocumentOverFulfillmentError(
    DocumentFulfillmentError
):
    """Raised when fulfilled quantity exceeds source quantity."""


def calculate_fulfilled_quantity(
    source_document_line_id: int,
    catalog: DocumentLineRelationCatalog,
) -> Decimal:
    """
    Calculate total quantity fulfilled for a source line.
    """

    if source_document_line_id <= 0:
        raise ValueError(
            "Source document line ID must be greater than zero"
        )

    relations = catalog.for_source(
        source_document_line_id
    )

    return sum(
        (
            relation.quantity
            for relation in relations
        ),
        start=Decimal("0"),
    )


def calculate_remaining_quantity(
    source_document_line_id: int,
    source_quantity: Decimal,
    catalog: DocumentLineRelationCatalog,
) -> Decimal:
    """
    Calculate quantity still remaining to be fulfilled.
    """

    if source_quantity < 0:
        raise ValueError(
            "Source quantity cannot be negative"
        )

    fulfilled_quantity = calculate_fulfilled_quantity(
        source_document_line_id=source_document_line_id,
        catalog=catalog,
    )

    if fulfilled_quantity > source_quantity:
        raise DocumentOverFulfillmentError(
            "Fulfilled quantity exceeds source quantity: "
            f"source_line_id={source_document_line_id}, "
            f"source_quantity={source_quantity}, "
            f"fulfilled_quantity={fulfilled_quantity}"
        )

    return source_quantity - fulfilled_quantity


def calculate_fulfillment_status(
    source_document_line_id: int,
    source_quantity: Decimal,
    catalog: DocumentLineRelationCatalog,
) -> FulfillmentStatus:
    """
    Determine fulfillment status of a source document line.
    """

    if source_quantity <= 0:
        raise ValueError(
            "Source quantity must be greater than zero"
        )

    fulfilled_quantity = calculate_fulfilled_quantity(
        source_document_line_id=source_document_line_id,
        catalog=catalog,
    )

    if fulfilled_quantity > source_quantity:
        raise DocumentOverFulfillmentError(
            "Fulfilled quantity exceeds source quantity: "
            f"source_line_id={source_document_line_id}, "
            f"source_quantity={source_quantity}, "
            f"fulfilled_quantity={fulfilled_quantity}"
        )

    if fulfilled_quantity == 0:
        return FulfillmentStatus.NOT_FULFILLED

    if fulfilled_quantity == source_quantity:
        return FulfillmentStatus.FULFILLED

    return FulfillmentStatus.PARTIALLY_FULFILLED