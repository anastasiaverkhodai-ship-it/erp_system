from dataclasses import dataclass

from app.services.document_relation_types import (
    DocumentRelationType,
)


@dataclass(frozen=True, slots=True)
class DocumentRelationDefinition:
    """
    Immutable relationship between two ERP documents.

    source_document_id
        The original or upstream document.

    target_document_id
        The downstream document related to the source.

    relation_type
        Semantic type of the relationship.
    """

    source_document_id: int
    target_document_id: int
    relation_type: DocumentRelationType

    def __post_init__(self) -> None:
        if self.source_document_id <= 0:
            raise ValueError(
                "Source document ID must be greater than zero"
            )

        if self.target_document_id <= 0:
            raise ValueError(
                "Target document ID must be greater than zero"
            )

        if self.source_document_id == self.target_document_id:
            raise ValueError(
                "A document cannot be related to itself"
            )