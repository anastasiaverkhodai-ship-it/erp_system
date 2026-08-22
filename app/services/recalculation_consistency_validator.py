from app.services.recalculation_coalescing_service import (
    RecalculationWindow,
)
from app.services.recalculation_request_definition import (
    RecalculationRequestDefinition,
)
from app.services.recalculation_types import (
    RecalculationStatus,
)


class RecalculationConsistencyValidator:
    """
    Validate that one coalesced recalculation window
    and its requests describe one consistent unit
    of recalculation work.
    """

    @staticmethod
    def validate_window(
        *,
        window: RecalculationWindow,
        requests: tuple[
            RecalculationRequestDefinition,
            ...
        ],
    ) -> None:
        """
        Validate:

        - window is not empty;
        - request_ids are unique;
        - supplied requests exactly match request_ids;
        - company/domain/stream are consistent;
        - effective_from is the true earliest point;
        - all requests have one lifecycle status.
        """

        if not window.request_ids:
            raise ValueError(
                "Recalculation window cannot be empty"
            )

        if len(set(window.request_ids)) != len(
            window.request_ids
        ):
            raise ValueError(
                "Recalculation window contains "
                "duplicate request_ids"
            )

        if len(requests) != len(
            window.request_ids
        ):
            raise ValueError(
                "Recalculation window request count "
                "does not match"
            )

        request_ids = tuple(
            request.request_id
            for request in requests
        )

        if request_ids != window.request_ids:
            raise ValueError(
                "Recalculation window request_ids "
                "do not match supplied requests"
            )

        for request in requests:
            if request.company_id != window.company_id:
                raise ValueError(
                    "Recalculation request company "
                    "does not match window"
                )

            if request.domain != window.domain:
                raise ValueError(
                    "Recalculation request domain "
                    "does not match window"
                )

            if request.stream_key != window.stream_key:
                raise ValueError(
                    "Recalculation request stream "
                    "does not match window"
                )

        earliest_effective_from = min(
            request.effective_from
            for request in requests
        )

        if (
            window.effective_from
            != earliest_effective_from
        ):
            raise ValueError(
                "Recalculation window effective_from "
                "is inconsistent with requests"
            )

        statuses = {
            request.status
            for request in requests
        }

        if len(statuses) != 1:
            raise ValueError(
                "Recalculation window requests "
                "must have one consistent status"
            )

    @staticmethod
    def get_status(
        *,
        window: RecalculationWindow,
        requests: tuple[
            RecalculationRequestDefinition,
            ...
        ],
    ) -> RecalculationStatus:
        """
        Return the common lifecycle status after
        validating the whole window.
        """

        RecalculationConsistencyValidator.validate_window(
            window=window,
            requests=requests,
        )

        return requests[0].status
