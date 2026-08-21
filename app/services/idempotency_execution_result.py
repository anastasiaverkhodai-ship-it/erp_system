from dataclasses import dataclass

from app.services.idempotency_decision_types import (
    IdempotencyDecision,
)
from app.services.idempotency_record_definition import (
    IdempotencyRecordDefinition,
)
from app.services.idempotency_result_definition import (
    IdempotencyResultDefinition,
)


@dataclass(frozen=True, slots=True)
class IdempotencyExecutionResult:
    """
    Result of evaluating an idempotent request.

    decision
        Action that should be taken.

    record
        Existing idempotency record, when one exists.

    reusable_result
        Stored result that may be reused when the
        decision is REUSE_RESULT.
    """

    decision: IdempotencyDecision
    record: IdempotencyRecordDefinition | None = None
    reusable_result: IdempotencyResultDefinition | None = None

    def __post_init__(self) -> None:
        if self.decision == IdempotencyDecision.START_NEW:
            if self.record is not None:
                raise ValueError(
                    "START_NEW cannot have an existing record"
                )

            if self.reusable_result is not None:
                raise ValueError(
                    "START_NEW cannot have a reusable result"
                )

            return

        if self.record is None:
            raise ValueError(
                "Existing idempotency record is required "
                f"for decision '{self.decision.value}'"
            )

        if self.decision == IdempotencyDecision.REUSE_RESULT:
            if self.reusable_result is None:
                raise ValueError(
                    "REUSE_RESULT requires a stored result"
                )

            return

        if self.reusable_result is not None:
            raise ValueError(
                "Reusable result is allowed only for "
                "REUSE_RESULT decision"
            )