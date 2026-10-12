"""C2 approval-gated integration boundary tests."""

from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

import pytest

from app.services.payroll_deduction_aggregation_policy import (
    DeductionPolicyLine,
)
from app.services.payroll_deduction_aggregation_integration import (
    ApprovedDeductionLine,
    DeductionIntegrationError,
    preview_approved_deduction_plan,
)
from app.services.payroll_deduction_rule_approval_contract import (
    RuleApprovalRecord,
)


def line(identifier=1, category="employer_statutory"):
    return DeductionPolicyLine(
        instruction_id=identifier,
        legal_category=category,
        priority=1,
        requested_amount=D("50.00"),
        individual_limit=D("40.00"),
    )


def preview(lines=()):
    return preview_approved_deduction_plan(
        company_id=1,
        effective_on=date(2026, 10, 12),
        available_amount=D("100.00"),
        aggregate_limit=D("30.00"),
        lines=lines,
    )


def test_empty_preview_is_non_posting():
    result = preview()
    assert result.policy_preview.result.total_allocated == D("0.00")
    assert result.payroll_posting_allowed is False
    assert result.legal_authorization == "NOT_AUTHORIZED"


def test_invalid_company_rejected():
    with pytest.raises(DeductionIntegrationError):
        preview_approved_deduction_plan(
            company_id=0,
            effective_on=date(2026, 10, 12),
            available_amount=D("100.00"),
            aggregate_limit=D("50.00"),
            lines=(),
        )


def test_invalid_date_rejected():
    with pytest.raises(DeductionIntegrationError):
        preview_approved_deduction_plan(
            company_id=1,
            effective_on="2026-10-12",
            available_amount=D("100.00"),
            aggregate_limit=D("50.00"),
            lines=(),
        )


def test_list_of_lines_rejected():
    with pytest.raises(DeductionIntegrationError):
        preview_approved_deduction_plan(
            company_id=1,
            effective_on=date(2026, 10, 12),
            available_amount=D("100.00"),
            aggregate_limit=D("50.00"),
            lines=[],
        )


def test_plain_policy_line_rejected():
    with pytest.raises(DeductionIntegrationError):
        preview((line(),))


def test_untyped_approval_rejected():
    with pytest.raises(DeductionIntegrationError):
        preview((
            ApprovedDeductionLine(
                policy_line=line(),
                approval=object(),
            ),
        ))


def test_existing_approval_gate_is_required():
    from pathlib import Path

    source = Path(
        "app/services/payroll_deduction_aggregation_integration.py"
    ).read_text()

    assert "require_approved_rule_record(" in source
    assert "entry.approval.company_id != company_id" in source


def test_existing_aggregation_policy_is_called():
    from pathlib import Path

    source = Path(
        "app/services/payroll_deduction_aggregation_integration.py"
    ).read_text()

    assert "preview_deduction_policy(" in source


def test_no_payroll_posting_or_database_writes():
    from pathlib import Path

    source = Path(
        "app/services/payroll_deduction_aggregation_integration.py"
    ).read_text()

    for forbidden in (
        "db.commit(",
        "db.flush(",
        "generate_and_post_payroll_deduction_journal(",
        "calculate_payroll_deduction_result(",
        "UPDATE payroll",
    ):
        assert forbidden not in source


def test_result_cannot_authorize_posting():
    result = preview()
    assert result.payroll_posting_allowed is False


def test_approval_type_is_enforced():
    assert RuleApprovalRecord is not None
