from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from app.services.recalculation_types import (
    RecalculationDomain,
    RecalculationStatus,
)


@dataclass(
    frozen=True,
    slots=True,
)
class RecalculationRequestDefinition:
    """
    Immutable definition of one backdated
    recalculation request.

    stream_key identifies the logical ERP stream
    affected by the backdated change.

    effective_from is the earliest point from which
    derived data may need to be recalculated.
    """

    company_id: int
    domain: RecalculationDomain
    stream_key: str
    effective_from: datetime

    request_id: str = field(
        default_factory=lambda: str(uuid4())
    )

    status: RecalculationStatus = (
        RecalculationStatus.PENDING
    )

    created_at: datetime = field(
        default_factory=datetime.utcnow
    )

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not self.stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        if not self.request_id.strip():
            raise ValueError(
                "request_id cannot be empty"
            )
