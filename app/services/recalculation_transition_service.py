from dataclasses import replace

from app.services.recalculation_request_definition import (
    RecalculationRequestDefinition,
)
from app.services.recalculation_types import (
    RecalculationStatus,
)


def start_recalculation(
    *,
    request: RecalculationRequestDefinition,
) -> RecalculationRequestDefinition:
    """
    Transition:

    PENDING -> PROCESSING
    """

    if request.status != RecalculationStatus.PENDING:
        raise ValueError(
            "Only PENDING recalculation request "
            "can be started"
        )

    return replace(
        request,
        status=RecalculationStatus.PROCESSING,
    )


def complete_recalculation(
    *,
    request: RecalculationRequestDefinition,
) -> RecalculationRequestDefinition:
    """
    Transition:

    PROCESSING -> COMPLETED
    """

    if request.status != RecalculationStatus.PROCESSING:
        raise ValueError(
            "Only PROCESSING recalculation request "
            "can be completed"
        )

    return replace(
        request,
        status=RecalculationStatus.COMPLETED,
    )


def fail_recalculation(
    *,
    request: RecalculationRequestDefinition,
) -> RecalculationRequestDefinition:
    """
    Transition:

    PROCESSING -> FAILED
    """

    if request.status != RecalculationStatus.PROCESSING:
        raise ValueError(
            "Only PROCESSING recalculation request "
            "can be failed"
        )

    return replace(
        request,
        status=RecalculationStatus.FAILED,
    )
