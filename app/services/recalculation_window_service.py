from datetime import datetime

from app.services.recalculation_request_catalog import (
    RecalculationRequestCatalog,
)
from app.services.recalculation_types import (
    RecalculationDomain,
)


class RecalculationWindowService:
    """
    Determine the effective recalculation window
    for one logical ERP stream.
    """

    def __init__(
        self,
        *,
        catalog: RecalculationRequestCatalog,
    ) -> None:
        self._catalog = catalog

    def get_effective_from(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
    ) -> datetime | None:
        """
        Return the earliest effective_from among
        recalculation requests for one stream.

        Returns None when the stream has no requests.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        requests = self._catalog.for_stream(
            company_id=company_id,
            domain=domain,
            stream_key=stream_key,
        )

        if not requests:
            return None

        return min(
            request.effective_from
            for request in requests
        )
