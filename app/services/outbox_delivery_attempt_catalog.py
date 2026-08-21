from app.services.outbox_delivery_attempt_definition import (
    OutboxDeliveryAttemptDefinition,
)


class OutboxDeliveryAttemptCatalogError(Exception):
    """
    Base error for outbox delivery attempt catalog.
    """


class OutboxDeliveryAttemptNotFoundError(
    OutboxDeliveryAttemptCatalogError
):
    """
    Raised when an outbox delivery attempt
    cannot be found.
    """


class DuplicateOutboxDeliveryAttemptError(
    OutboxDeliveryAttemptCatalogError
):
    """
    Raised when duplicate
    (event_id, attempt_number) is registered.
    """


class OutboxDeliveryAttemptCatalog:
    def __init__(
        self,
        attempts: tuple[
            OutboxDeliveryAttemptDefinition,
            ...
        ],
    ) -> None:
        self._by_key: dict[
            tuple[str, int],
            OutboxDeliveryAttemptDefinition,
        ] = {}

        for attempt in attempts:
            key = (
                attempt.event_id,
                attempt.attempt_number,
            )

            if key in self._by_key:
                raise DuplicateOutboxDeliveryAttemptError(
                    "Duplicate outbox delivery attempt: "
                    f"event_id='{attempt.event_id}', "
                    f"attempt_number={attempt.attempt_number}"
                )

            self._by_key[key] = attempt

    def get(
        self,
        *,
        event_id: str,
        attempt_number: int,
    ) -> OutboxDeliveryAttemptDefinition:
        key = (
            event_id,
            attempt_number,
        )

        try:
            return self._by_key[key]
        except KeyError as exc:
            raise OutboxDeliveryAttemptNotFoundError(
                "Outbox delivery attempt not found: "
                f"event_id='{event_id}', "
                f"attempt_number={attempt_number}"
            ) from exc

    def for_event(
        self,
        event_id: str,
    ) -> tuple[
        OutboxDeliveryAttemptDefinition,
        ...
    ]:
        return tuple(
            attempt
            for attempt in self._by_key.values()
            if attempt.event_id == event_id
        )

    def all(
        self,
    ) -> tuple[
        OutboxDeliveryAttemptDefinition,
        ...
    ]:
        return tuple(
            self._by_key.values()
        )