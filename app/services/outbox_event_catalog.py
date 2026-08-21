from app.services.outbox_event_definition import (
    OutboxEventDefinition,
)


class OutboxEventCatalogError(Exception):
    """
    Base error for outbox event catalog.
    """


class OutboxEventNotFoundError(
    OutboxEventCatalogError
):
    """
    Raised when an outbox event cannot be found.
    """


class DuplicateOutboxEventError(
    OutboxEventCatalogError
):
    """
    Raised when duplicate event_id is registered.
    """


class OutboxEventCatalog:
    def __init__(
        self,
        events: tuple[OutboxEventDefinition, ...],
    ) -> None:
        self._by_event_id: dict[
            str,
            OutboxEventDefinition,
        ] = {}

        for event in events:
            if event.event_id in self._by_event_id:
                raise DuplicateOutboxEventError(
                    "Duplicate outbox event: "
                    f"event_id='{event.event_id}'"
                )

            self._by_event_id[
                event.event_id
            ] = event

    def get(
        self,
        event_id: str,
    ) -> OutboxEventDefinition:
        try:
            return self._by_event_id[event_id]
        except KeyError as exc:
            raise OutboxEventNotFoundError(
                "Outbox event not found: "
                f"event_id='{event_id}'"
            ) from exc

    def for_company(
        self,
        company_id: int,
    ) -> tuple[OutboxEventDefinition, ...]:
        return tuple(
            event
            for event in self._by_event_id.values()
            if event.company_id == company_id
        )

    def all(
        self,
    ) -> tuple[OutboxEventDefinition, ...]:
        return tuple(
            self._by_event_id.values()
        )