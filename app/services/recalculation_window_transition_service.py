from app.services.recalculation_coalescing_service import (
    RecalculationWindow,
)
from app.services.recalculation_request_catalog import (
    RecalculationRequestCatalog,
)
from app.services.recalculation_request_definition import (
    RecalculationRequestDefinition,
)
from app.services.recalculation_transition_service import (
    complete_recalculation,
    fail_recalculation,
    start_recalculation,
)
from app.services.recalculation_types import (
    RecalculationStatus,
)


class RecalculationWindowTransitionService:
    """
    Apply one lifecycle transition consistently
    to every request contained in one coalesced
    recalculation window.

    This pure-domain service does not mutate
    the source catalog.
    """

    def __init__(
        self,
        *,
        catalog: RecalculationRequestCatalog,
    ) -> None:
        self._catalog = catalog

    def start_window(
        self,
        *,
        window: RecalculationWindow,
    ) -> tuple[
        RecalculationRequestDefinition,
        ...
    ]:
        """
        Transition every request in the window:

        PENDING -> PROCESSING
        """

        requests = self._get_window_requests(
            window=window,
        )

        self._require_status(
            requests=requests,
            expected=RecalculationStatus.PENDING,
        )

        return tuple(
            start_recalculation(
                request=request,
            )
            for request in requests
        )

    def complete_window(
        self,
        *,
        window: RecalculationWindow,
        requests: tuple[
            RecalculationRequestDefinition,
            ...
        ],
    ) -> tuple[
        RecalculationRequestDefinition,
        ...
    ]:
        """
        Transition every supplied window request:

        PROCESSING -> COMPLETED
        """

        self._validate_supplied_requests(
            window=window,
            requests=requests,
        )

        self._require_status(
            requests=requests,
            expected=RecalculationStatus.PROCESSING,
        )

        return tuple(
            complete_recalculation(
                request=request,
            )
            for request in requests
        )

    def fail_window(
        self,
        *,
        window: RecalculationWindow,
        requests: tuple[
            RecalculationRequestDefinition,
            ...
        ],
    ) -> tuple[
        RecalculationRequestDefinition,
        ...
    ]:
        """
        Transition every supplied window request:

        PROCESSING -> FAILED
        """

        self._validate_supplied_requests(
            window=window,
            requests=requests,
        )

        self._require_status(
            requests=requests,
            expected=RecalculationStatus.PROCESSING,
        )

        return tuple(
            fail_recalculation(
                request=request,
            )
            for request in requests
        )

    def _get_window_requests(
        self,
        *,
        window: RecalculationWindow,
    ) -> tuple[
        RecalculationRequestDefinition,
        ...
    ]:
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

        requests: list[
            RecalculationRequestDefinition
        ] = []

        for request_id in window.request_ids:
            request = self._catalog.get(
                request_id=request_id,
            )

            if request is None:
                raise ValueError(
                    "Recalculation window references "
                    "an unknown request_id"
                )

            requests.append(
                request
            )

        result = tuple(
            requests
        )

        self._validate_supplied_requests(
            window=window,
            requests=result,
        )

        return result

    def _validate_supplied_requests(
        self,
        *,
        window: RecalculationWindow,
        requests: tuple[
            RecalculationRequestDefinition,
            ...
        ],
    ) -> None:
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

            if (
                request.effective_from
                < window.effective_from
            ):
                raise ValueError(
                    "Recalculation request starts "
                    "before window effective_from"
                )

    @staticmethod
    def _require_status(
        *,
        requests: tuple[
            RecalculationRequestDefinition,
            ...
        ],
        expected: RecalculationStatus,
    ) -> None:
        if any(
            request.status != expected
            for request in requests
        ):
            raise ValueError(
                "All recalculation window requests "
                f"must have status {expected.value}"
            )
