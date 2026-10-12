"""Fail-closed tests for legal rule review."""

import pytest

from app.services.payroll_deduction_legal_classification import (
    CATEGORIES,
)
from app.services.payroll_deduction_legal_rule_contract import (
    PayrollDeductionLegalRuleError,
    get_legal_rule_review,
    require_approved_legal_rule,
)


@pytest.mark.parametrize("category", list(CATEGORIES))
def test_candidate_rules_require_legal_review(category):
    review = get_legal_rule_review(category)

    assert review.category == category
    assert review.legally_approved is False
    assert review.automatic_payroll_application_allowed is False


@pytest.mark.parametrize("category", list(CATEGORIES))
def test_unapproved_rules_cannot_activate_payroll(category):
    with pytest.raises(
        PayrollDeductionLegalRuleError,
        match="not been approved",
    ):
        require_approved_legal_rule(category)


def test_unknown_category_rejected():
    with pytest.raises(
        PayrollDeductionLegalRuleError,
        match="Unknown",
    ):
        get_legal_rule_review("unknown")


def test_candidate_percentages_match_classification():
    for category, classification in CATEGORIES.items():
        review = get_legal_rule_review(category)

        assert (
            review.candidate_individual_percentage
            == classification.individual_limit_percentage
        )

        assert (
            review.candidate_aggregate_percentage
            == classification.aggregate_limit_percentage
        )
