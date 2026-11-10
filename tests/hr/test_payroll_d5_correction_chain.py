import copy

import pytest

from app.services.payroll_d5_correction_chain import (
    D5CorrectionChainError,
    assess_d5_reversal_chain,
)


def source():
    return {
        "id": 10,
        "company_id": 1,
        "contract_id": 20,
        "event_type": "hire",
        "effective_date": "2026-01-10",
        "order_number": "HIRE-1",
        "reversal_of_id": None,
    }


def reversal():
    return {
        "id": 11,
        "company_id": 1,
        "contract_id": 20,
        "event_type": "reversal",
        "effective_date": "2026-01-12",
        "order_number": "REV-1",
        "reversal_of_id": 10,
    }


def review(rows, blockers=()):
    return assess_d5_reversal_chain(
        company_id=1,
        employment_event_id=10,
        snapshots=rows,
        extra_blockers=blockers,
    )


def test_original_without_reversal():
    result = review([source()])
    assert result["chain_state"] == "NO_REVERSAL_RECORDED"
    assert result["reversal_count"] == 0
    assert result["official_generation"] == "BLOCKED"


def test_one_reversal():
    result = review([source(), reversal()])
    assert result["chain_state"] == "SOURCE_REVERSED"
    assert result["chain_event_ids"] == [10, 11]
    assert result["official_rows_generated"] == 0


def test_repeatable_digest():
    first = review([source(), reversal()])
    second = review([source(), reversal()])
    assert first["correction_chain_sha256"] == (
        second["correction_chain_sha256"]
    )


def test_reversal_of_reversal_requires_review():
    second = copy.deepcopy(reversal())
    second.update({
        "id": 12,
        "reversal_of_id": 11,
        "order_number": "REV-2",
    })

    result = review([source(), reversal(), second])

    assert result["chain_state"] == "REVIEW_BLOCKED"
    assert "REVERSAL_OF_REVERSAL_REQUIRES_REVIEW" in (
        result["blockers"]
    )


def test_cross_company_blocked():
    bad = reversal()
    bad["company_id"] = 99

    result = review([source(), bad])
    assert "CROSS_COMPANY_CORRECTION" in result["blockers"]


def test_cross_contract_blocked():
    bad = reversal()
    bad["contract_id"] = 999

    result = review([source(), bad])
    assert "CROSS_CONTRACT_CORRECTION" in result["blockers"]


def test_wrong_event_type_blocked():
    bad = reversal()
    bad["event_type"] = "hire"

    result = review([source(), bad])
    assert "INVALID_CORRECTION_TYPE" in result["blockers"]


def test_broken_link_blocked():
    bad = reversal()
    bad["reversal_of_id"] = 999

    result = review([source(), bad])
    assert "BROKEN_CORRECTION_LINK" in result["blockers"]


def test_ambiguous_branch_blocked():
    result = review(
        [source()],
        ["AMBIGUOUS_CORRECTION_BRANCH"],
    )
    assert result["chain_state"] == "REVIEW_BLOCKED"


def test_invalid_original_rejected():
    with pytest.raises(
        D5CorrectionChainError,
        match="Source must not itself",
    ):
        bad = source()
        bad["reversal_of_id"] = 5
        review([bad])


def test_mismatched_source_identity_rejected():
    bad = source()
    bad["id"] = 500

    with pytest.raises(
        D5CorrectionChainError,
        match="identity mismatch",
    ):
        review([bad])


def test_missing_order_blocked():
    bad = reversal()
    bad["order_number"] = ""

    assert "MISSING_CORRECTION_ORDER" in (
        review([source(), bad])["blockers"]
    )


@pytest.mark.parametrize("bad", [None, True, 0, -1, "1"])
def test_invalid_company_rejected(bad):
    with pytest.raises(D5CorrectionChainError):
        assess_d5_reversal_chain(
            company_id=bad,
            employment_event_id=10,
            snapshots=[source()],
        )
