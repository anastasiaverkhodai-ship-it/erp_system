"""Verified request contract for payroll recalculation.

Phase 13.8-E1 supports changes to minimum-wage context only.

This module validates request intent. It does not calculate payroll,
create a revision, link a correction, post accounting or pay wages.
"""

from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from app.services.payroll_minimum_wage_service import (
    MinimumWageError,
    normalize_minimum_wage_context,
)


class PayrollCorrectionRecalculationError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedCorrectionRequest:
    company_id: int
    original_calculation_id: int
    request_key: str
    reason: str
    minimum_wage_context: dict[str, Any]
    request_sha256: str


def _json_value(value: Any) -> Any:
    if value is None or type(value) in (str, int, bool):
        return value

    if isinstance(value, Decimal):
        if not value.is_finite():
            raise PayrollCorrectionRecalculationError(
                "Non-finite decimal value is forbidden"
            )
        return str(value)

    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]

    if isinstance(value, dict):
        if not all(type(key) is str for key in value):
            raise PayrollCorrectionRecalculationError(
                "Context keys must be strings"
            )

        return {
            key: _json_value(item)
            for key, item in sorted(value.items())
        }

    raise PayrollCorrectionRecalculationError(
        f"Unsupported context value: {type(value).__name__}"
    )


def _fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _json_value(dict(payload)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(canonical).hexdigest()


def verify_minimum_wage_correction_request(
    *,
    company_id: int,
    original_calculation_id: int,
    request_key: str,
    reason: str,
    existing_minimum_wage_assessment: dict | None,
    corrected_minimum_wage_context: dict,
) -> VerifiedCorrectionRequest:
    """Validate a correction before attempting any database write."""

    if type(company_id) is not int or company_id <= 0:
        raise PayrollCorrectionRecalculationError(
            "Valid company_id is required"
        )

    if (
        type(original_calculation_id) is not int
        or original_calculation_id <= 0
    ):
        raise PayrollCorrectionRecalculationError(
            "Valid original_calculation_id is required"
        )

    if not isinstance(request_key, str):
        raise PayrollCorrectionRecalculationError(
            "request_key must be a string"
        )

    request_key = request_key.strip()

    if not request_key or len(request_key) > 100:
        raise PayrollCorrectionRecalculationError(
            "request_key must contain 1 to 100 characters"
        )

    if not isinstance(reason, str) or not reason.strip():
        raise PayrollCorrectionRecalculationError(
            "Correction reason is required"
        )

    reason = reason.strip()

    if len(reason) > 2000:
        raise PayrollCorrectionRecalculationError(
            "Correction reason is too long"
        )

    if not isinstance(corrected_minimum_wage_context, dict):
        raise PayrollCorrectionRecalculationError(
            "Corrected minimum-wage context must be a dictionary"
        )

    if not isinstance(existing_minimum_wage_assessment, (dict, type(None))):
        raise PayrollCorrectionRecalculationError(
            "Saved minimum-wage assessment has invalid format"
        )

    try:
        corrected = normalize_minimum_wage_context(
            corrected_minimum_wage_context
        )
    except MinimumWageError as exc:
        raise PayrollCorrectionRecalculationError(
            str(exc)
        ) from exc

    if not isinstance(corrected, dict):
        raise PayrollCorrectionRecalculationError(
            "Minimum-wage context normalization must produce a dictionary"
        )

    saved_request = (
        (existing_minimum_wage_assessment or {}).get("request")
    )

    if saved_request is not None and not isinstance(saved_request, dict):
        raise PayrollCorrectionRecalculationError(
            "Saved minimum-wage request has invalid format"
        )

    if corrected == (saved_request or {}):
        raise PayrollCorrectionRecalculationError(
            "Corrected minimum-wage context is unchanged"
        )

    payload = {
        "schema": "payroll-minimum-wage-correction-request-v1",
        "company_id": company_id,
        "original_calculation_id": original_calculation_id,
        "request_key": request_key,
        "reason": reason,
        "minimum_wage_context": corrected,
    }

    return VerifiedCorrectionRequest(
        company_id=company_id,
        original_calculation_id=original_calculation_id,
        request_key=request_key,
        reason=reason,
        minimum_wage_context=dict(corrected),
        request_sha256=_fingerprint(payload),
    )
