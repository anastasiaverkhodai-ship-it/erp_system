from dataclasses import dataclass
from typing import Protocol


@dataclass(
    frozen=True,
    slots=True,
)
class OutboxPublishMessage:
    """
    Immutable message passed from the outbox worker
    to an external publisher adapter.

    event_id is stable and must be preserved by the
    publisher so consumers can deduplicate deliveries.
    """

    event_id: str
    company_id: int
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: str
    attempt_number: int


class OutboxPublisher(Protocol):
    """
    Contract for an external outbox publisher.

    Implementations may publish to Redis, RabbitMQ,
    Kafka, HTTP, or another transport.

    Successful delivery returns normally.

    Delivery failure must raise an exception.
    """

    async def publish(
        self,
        *,
        message: OutboxPublishMessage,
    ) -> None:
        ...
