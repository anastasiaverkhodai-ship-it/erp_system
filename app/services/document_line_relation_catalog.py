from app.services.document_line_relation_definition import (
    DocumentLineRelationDefinition,
)


class DocumentLineRelationCatalogError(Exception):
    """Base error for document line relation catalog operations."""


class DocumentLineRelationNotFoundError(
    DocumentLineRelationCatalogError
):
    """Raised when a document line relation is not registered."""


class DuplicateDocumentLineRelationError(
    DocumentLineRelationCatalogError
):
    """Raised when the same line relation is registered twice."""


class DocumentLineRelationCatalog:
    def __init__(
        self,
        relations: tuple[
            DocumentLineRelationDefinition,
            ...,
        ],
    ) -> None:
        self._relations: dict[
            tuple[int, int],
            DocumentLineRelationDefinition,
        ] = {}

        for relation in relations:
            key = (
                relation.source_document_line_id,
                relation.target_document_line_id,
            )

            if key in self._relations:
                raise DuplicateDocumentLineRelationError(
                    "Duplicate document line relation: "
                    f"{relation.source_document_line_id} -> "
                    f"{relation.target_document_line_id}"
                )

            self._relations[key] = relation

    def get(
        self,
        source_document_line_id: int,
        target_document_line_id: int,
    ) -> DocumentLineRelationDefinition:
        key = (
            source_document_line_id,
            target_document_line_id,
        )

        relation = self._relations.get(key)

        if relation is None:
            raise DocumentLineRelationNotFoundError(
                "Document line relation is not registered: "
                f"{source_document_line_id} -> "
                f"{target_document_line_id}"
            )

        return relation

    def for_source(
        self,
        source_document_line_id: int,
    ) -> tuple[DocumentLineRelationDefinition, ...]:
        return tuple(
            relation
            for relation in self._relations.values()
            if (
                relation.source_document_line_id
                == source_document_line_id
            )
        )

    def all(
        self,
    ) -> tuple[DocumentLineRelationDefinition, ...]:
        return tuple(self._relations.values())