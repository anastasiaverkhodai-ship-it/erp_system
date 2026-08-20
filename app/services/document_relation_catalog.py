from app.services.document_relation_definition import (
    DocumentRelationDefinition,
)
from app.services.document_relation_types import (
    DocumentRelationType,
)


class DocumentRelationCatalogError(Exception):
    """Base error for document relation catalog operations."""


class DocumentRelationNotFoundError(
    DocumentRelationCatalogError
):
    """Raised when a document relation is not registered."""


class DuplicateDocumentRelationError(
    DocumentRelationCatalogError
):
    """Raised when the same document relation is registered twice."""


class DocumentRelationCatalog:
    def __init__(
        self,
        relations: tuple[
            DocumentRelationDefinition,
            ...,
        ],
    ) -> None:
        self._relations: dict[
            tuple[
                int,
                int,
                DocumentRelationType,
            ],
            DocumentRelationDefinition,
        ] = {}

        for relation in relations:
            key = (
                relation.source_document_id,
                relation.target_document_id,
                relation.relation_type,
            )

            if key in self._relations:
                raise DuplicateDocumentRelationError(
                    "Duplicate document relation: "
                    f"{relation.source_document_id} -> "
                    f"{relation.target_document_id} "
                    f"({relation.relation_type})"
                )

            self._relations[key] = relation

    def get(
        self,
        source_document_id: int,
        target_document_id: int,
        relation_type: DocumentRelationType,
    ) -> DocumentRelationDefinition:
        key = (
            source_document_id,
            target_document_id,
            relation_type,
        )

        relation = self._relations.get(key)

        if relation is None:
            raise DocumentRelationNotFoundError(
                "Document relation is not registered: "
                f"{source_document_id} -> "
                f"{target_document_id} "
                f"({relation_type})"
            )

        return relation

    def all(
        self,
    ) -> tuple[DocumentRelationDefinition, ...]:
        return tuple(self._relations.values())