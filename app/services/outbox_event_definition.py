from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from app.services.outbox_types import OutboxStatus


@dataclass(frozen=True, slots=True)
class OutboxEventDefinition:
    company_id: int
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: str

    event_id: str = field(
        default_factory=lambda: str(uuid4())
    )

    status: OutboxStatus = OutboxStatus.PENDING
    occurred_at: datetime = field(
        default_factory=datetime.utcnow
    )

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "company_id must be greater than zero"
            )

        if not self.event_type.strip():
            raise ValueError(
                "event_type cannot be empty"
            )

        if not self.aggregate_type.strip():
            raise ValueError(
                "aggregate_type cannot be empty"
            )

        if not self.aggregate_id.strip():
            raise ValueError(
                "aggregate_id cannot be empty"
            )

        if not self.payload.strip():
            raise ValueError(
                "payload cannot be empty"
            )

        if not self.event_id.strip():
            raise ValueError(
                "event_id cannot be empty"
            )