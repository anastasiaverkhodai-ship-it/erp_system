from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DocumentLineRelationDefinition:
    """
    Immutable relationship between two document lines.

    source_document_line_id
        Line of the upstream document.

    target_document_line_id
        Line of the downstream document.

    quantity
        Quantity from the source line represented
        by this relation.
    """

    source_document_line_id: int
    target_document_line_id: int
    quantity: Decimal

    def __post_init__(self) -> None:
        if self.source_document_line_id <= 0:
            raise ValueError(
                "Source document line ID must be greater than zero"
            )

        if self.target_document_line_id <= 0:
            raise ValueError(
                "Target document line ID must be greater than zero"
            )

        if (
            self.source_document_line_id
            == self.target_document_line_id
        ):
            raise ValueError(
                "A document line cannot be related to itself"
            )

        if self.quantity <= 0:
            raise ValueError(
                "Document line relation quantity "
                "must be greater than zero"
            )