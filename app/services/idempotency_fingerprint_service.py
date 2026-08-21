import hashlib
import json
from typing import Any


class IdempotencyFingerprintError(Exception):
    """
    Base error for request fingerprint generation.
    """


class IdempotencyFingerprintSerializationError(
    IdempotencyFingerprintError
):
    """
    Raised when request payload cannot be serialized
    into canonical JSON.
    """


def generate_request_fingerprint(
    payload: Any,
) -> str:
    """
    Generate a deterministic SHA-256 fingerprint
    for a JSON-compatible request payload.

    Equivalent payloads with different dictionary
    key order produce the same fingerprint.
    """

    try:
        canonical_json = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise IdempotencyFingerprintSerializationError(
            "Request payload cannot be serialized "
            "into canonical JSON"
        ) from exc

    return hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()