from copy import deepcopy

import pytest

from app.services.payroll_regulatory_4df_combined_source_binding import (
    FourDFCombinedBindingError,
    _digest,
    _guard,
    _positive,
)


def valid_review():
    body = {
        "schema": "4DF_COMBINED_SOURCE_BINDING_V1",
        "company_id": 1,
        "payroll_period_id": 2,
        "combined_review_sha256": "a" * 64,
        "four_df_status": "NOT_VERIFIED",
        "source_binding": None,
        "source_count": 0,
        "slice_count": 0,
        "independent_fingerprint_verification": False,
        "gross_paid_income_verified": False,
        "statutory_mapping_approved": False,
        "review_only": True,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
    return {
        "body": body,
        "review_sha256": _digest(body),
    }


def test_review_only_gate():
    assert _guard(valid_review()) is True


def test_digest_is_order_independent():
    assert _digest({"a": 1, "b": 2}) == _digest(
        {"b": 2, "a": 1}
    )


@pytest.mark.parametrize(
    "value", [0, -1, True, None, "2"]
)
def test_invalid_identifier(value):
    with pytest.raises(FourDFCombinedBindingError):
        _positive(value, "identifier")


def test_tampering_blocked():
    report = valid_review()
    report["body"]["source_count"] = 1

    with pytest.raises(FourDFCombinedBindingError):
        _guard(report)


def test_official_generation_blocked():
    report = valid_review()
    report["body"]["official_generation"] = "ENABLED"
    report["review_sha256"] = _digest(report["body"])

    with pytest.raises(FourDFCombinedBindingError):
        _guard(report)


def test_gross_payment_claim_blocked():
    report = valid_review()
    report["body"]["gross_paid_income_verified"] = True
    report["review_sha256"] = _digest(report["body"])

    with pytest.raises(FourDFCombinedBindingError):
        _guard(report)


def test_structural_state_requires_binding():
    report = valid_review()
    report["body"]["four_df_status"] = (
        "SLICE_TOPOLOGY_VERIFIED"
    )
    report["review_sha256"] = _digest(report["body"])

    with pytest.raises(FourDFCombinedBindingError):
        _guard(report)


def test_input_not_modified():
    original = valid_review()
    copied = deepcopy(original)
    _guard(original)
    assert original == copied
