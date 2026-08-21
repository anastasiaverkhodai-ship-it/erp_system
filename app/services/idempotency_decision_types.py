from enum import StrEnum


class IdempotencyDecision(StrEnum):
    START_NEW = "start_new"
    ALREADY_IN_PROGRESS = "already_in_progress"
    REUSE_RESULT = "reuse_result"

    FAILED_NOT_RETRYABLE = "failed_not_retryable"
    RETRY_FAILED = "retry_failed"