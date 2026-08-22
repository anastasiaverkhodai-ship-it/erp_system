from app.services.recalculation_request_definition import (
    RecalculationRequestDefinition,
)
from app.services.recalculation_types import (
    RecalculationDomain,
)


class RecalculationRequestCatalog:
    """
    In-memory catalog of recalculation requests.

    Used by pure domain logic before persistence
    is introduced.
    """

    def __init__(
        self,
        requests: tuple[
            RecalculationRequestDefinition,
            ...
        ] = (),
    ) -> None:
        self._requests_by_id: dict[
            str,
            RecalculationRequestDefinition,
        ] = {}

        for request in requests:
            self.add(
                request=request,
            )

    def add(
        self,
        *,
        request: RecalculationRequestDefinition,
    ) -> None:
        if request.request_id in self._requests_by_id:
            raise ValueError(
                "Duplicate recalculation request_id"
            )

        self._requests_by_id[
            request.request_id
        ] = request

    def get(
        self,
        *,
        request_id: str,
    ) -> RecalculationRequestDefinition | None:
        return self._requests_by_id.get(
            request_id
        )

    def for_stream(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
    ) -> tuple[
        RecalculationRequestDefinition,
        ...
    ]:
        return tuple(
            request
            for request in self._requests_by_id.values()
            if (
                request.company_id == company_id
                and request.domain == domain
                and request.stream_key == stream_key
            )
        )

    def all(
        self,
    ) -> tuple[
        RecalculationRequestDefinition,
        ...
    ]:
        return tuple(
            self._requests_by_id.values()
        )
