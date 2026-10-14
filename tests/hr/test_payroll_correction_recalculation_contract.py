"""Contracts for safe minimum-wage correction requests."""

import pytest

from app.services.payroll_correction_recalculation_contract import (
    PayrollCorrectionRecalculationError,
    verify_minimum_wage_correction_request,
)


def _verify(**changes):
    args = {
        "company_id": 1,
        "original_calculation_id": 2,
        "request_key": "correction-001",
        "reason": "Correct classification",
        "existing_minimum_wage_assessment": {
            "request": {
                "full_time_norm_minutes": 9600,
                "norm_reference": "Payroll correction documentary evidence",
            }
        },
        "corrected_minimum_wage_context": {
            "full_time_norm_minutes": 10000,
            "norm_reference": "Payroll correction documentary evidence",
        },
    }

    args.update(changes)

    return verify_minimum_wage_correction_request(**args)


def test_request_fingerprint_is_deterministic():
    first = _verify()
    second = _verify()

    assert first.request_sha256 == second.request_sha256
    assert len(first.request_sha256) == 64
    assert first.minimum_wage_context == second.minimum_wage_context


def test_changed_context_has_different_fingerprint():
    first = _verify()

    second = _verify(
        corrected_minimum_wage_context={
            "full_time_norm_minutes": 11000,
            "norm_reference": "Payroll correction documentary evidence",
        }
    )

    assert first.request_sha256 != second.request_sha256


def test_unchanged_context_is_rejected():
    with pytest.raises(
        PayrollCorrectionRecalculationError,
        match="unchanged",
    ):
        _verify(
            corrected_minimum_wage_context={
                "full_time_norm_minutes": 9600,
                "norm_reference": "Payroll correction documentary evidence",
            }
        )


def test_empty_request_key_is_rejected():
    with pytest.raises(
        PayrollCorrectionRecalculationError,
        match="request_key",
    ):
        _verify(request_key=" ")


def test_invalid_company_is_rejected():
    with pytest.raises(
        PayrollCorrectionRecalculationError,
        match="company_id",
    ):
        _verify(company_id=0)


def test_empty_reason_is_rejected():
    with pytest.raises(
        PayrollCorrectionRecalculationError,
        match="reason",
    ):
        _verify(reason=" ")


def test_invalid_context_is_rejected():
    with pytest.raises(PayrollCorrectionRecalculationError):
        _verify(corrected_minimum_wage_context="invalid")


def test_request_key_is_part_of_fingerprint():
    first = _verify()
    second = _verify(request_key="correction-002")

    assert first.request_sha256 != second.request_sha256
