"""D5 normative readiness contract."""

from app.services.payroll_d5_normative_acceptance import (
    BLOCKER,
    REQUIRED_SOURCE_KINDS,
    assess_d5_normative_candidates,
)
from app.services.payroll_regulatory_readiness_service import (
    FORM_BLOCKERS,
)


def test_normative_blocker_connected():
    assert BLOCKER in FORM_BLOCKERS["d5"]


def test_existing_d5_mapping_blocker_preserved():
    assert (
        "D5_PROFESSIONAL_DETAILS_AND_EVENT_MAPPING_MISSING"
        in FORM_BLOCKERS["d5"]
    )


def test_unrelated_form_blockers_unchanged():
    for kind in ("d1", "4df", "d6"):
        assert BLOCKER not in FORM_BLOCKERS[kind]


def test_no_normative_approval():
    result = assess_d5_normative_candidates()

    assert result.status == "REVIEW_REQUIRED"
    assert result.official_generation == "BLOCKED"
    assert result.official_rows_generated == 0
    assert BLOCKER in result.blockers


def test_all_source_kinds_still_unapproved():
    result = assess_d5_normative_candidates()

    assert set(result.missing_source_kinds) == (
        REQUIRED_SOURCE_KINDS
    )
