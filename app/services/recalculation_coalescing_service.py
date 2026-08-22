from dataclasses import dataclass
from datetime import datetime

from app.services.recalculation_request_catalog import (
    RecalculationRequestCatalog,
)
from app.services.recalculation_types import (
    RecalculationDomain,
    RecalculationStatus,
)


@dataclass(
    frozen=True,
    slots=True,
)
class RecalculationWindow:
    """
    One coalesced recalculation window for a
    logical ERP stream.
    """

    company_id: int
    domain: RecalculationDomain
    stream_key: str
    effective_from: datetime
    request_ids: tuple[str, ...]

    @property
    def request_count(
        self,
    ) -> int:
        return len(
            self.request_ids
        )


class RecalculationCoalescingService:
    """
    Coalesce multiple PENDING recalculation requests
    for one stream into one recalculation window.
    """

    def __init__(
        self,
        *,
        catalog: RecalculationRequestCatalog,
    ) -> None:
        self._catalog = catalog

    def coalesce_stream(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
    ) -> RecalculationWindow | None:
        """
        Return one coalesced window containing all
        PENDING requests for the selected stream.

        The earliest effective_from becomes the start
        of the recalculation window.

        Non-PENDING requests are ignored.

        Returns None when there are no PENDING
        requests for the stream.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        requests = tuple(
            request
            for request in self._catalog.for_stream(
                company_id=company_id,
                domain=domain,
                stream_key=stream_key,
            )
            if request.status
            == RecalculationStatus.PENDING
        )

        if not requests:
            return None

        ordered_requests = tuple(
            sorted(
                requests,
                key=lambda request: (
                    request.effective_from,
                    request.created_at,
                    request.request_id,
                ),
            )
        )

        return RecalculationWindow(
            company_id=company_id,
            domain=domain,
            stream_key=stream_key,
            effective_from=(
                ordered_requests[0].effective_from
            ),
            request_ids=tuple(
                request.request_id
                for request in ordered_requests
            ),
        )
