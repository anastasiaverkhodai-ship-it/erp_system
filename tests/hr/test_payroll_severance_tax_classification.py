"""Dated severance classification contract tests."""

from datetime import date
from decimal import Decimal

import pytest

from app.services.payroll_severance_income_classification import (
    PayrollIncomeSplit,
)
from app.services.payroll_severance_tax_classification import (
    SeveranceTaxClassificationError as Error,
    SeveranceTaxPolicy,
    classify_severance_tax_bases,
    select_severance_tax_policy,
)


def policy(**changes):
    data = dict(
        code="UA-ORDINARY-SEVERANCE-2026",
        valid_from=date(2026, 8, 1),
        valid_to=None,
        source_reference="DPS ZIR / dated rule evidence",
        personal_income_tax_included=True,
        military_levy_included=True,
        unified_social_contribution_included=False,
        income_code_4df="127",
    )
    data.update(changes)
    return SeveranceTaxPolicy(**data)


def income(ordinary="10000.00", severance="5000.00"):
    ordinary = Decimal(ordinary)
    severance = Decimal(severance)
    return PayrollIncomeSplit(
        ordinary_gross=ordinary,
        severance_gross=severance,
        total_gross=ordinary + severance,
        severance_source_ids=(41,) if severance else (),
    )


def test_mixed_income_excludes_severance_from_usc():
    result = classify_severance_tax_bases(
        split=income(),
        income_date=date(2026, 10, 20),
        policies=[policy()],
    )
    assert result.personal_income_tax_base == Decimal("15000.00")
    assert result.military_levy_base == Decimal("15000.00")
    assert result.unified_social_contribution_base == Decimal("10000.00")
    assert result.income_code_4df == "127"


def test_severance_only_has_zero_usc_earnings_base():
    result = classify_severance_tax_bases(
        split=income("0.00", "4000.00"),
        income_date=date(2026, 10, 20),
        policies=[policy()],
    )
    assert result.unified_social_contribution_base == Decimal("0.00")
    assert result.personal_income_tax_base == Decimal("4000.00")


def test_policy_before_effective_date_is_rejected():
    with pytest.raises(Error):
        select_severance_tax_policy(
            income_date=date(2026, 7, 31),
            policies=[policy()],
        )


def test_missing_policy_is_rejected():
    with pytest.raises(Error):
        select_severance_tax_policy(
            income_date=date(2026, 10, 20),
            policies=[],
        )


def test_overlapping_policies_are_rejected():
    with pytest.raises(Error):
        select_severance_tax_policy(
            income_date=date(2026, 10, 20),
            policies=[
                policy(code="A"),
                policy(code="B"),
            ],
        )


def test_expired_policy_is_rejected():
    with pytest.raises(Error):
        select_severance_tax_policy(
            income_date=date(2026, 10, 20),
            policies=[policy(valid_to=date(2026, 9, 30))],
        )


def test_missing_source_reference_is_rejected():
    with pytest.raises(Error):
        select_severance_tax_policy(
            income_date=date(2026, 10, 20),
            policies=[policy(source_reference="")],
        )


def test_wrong_income_code_is_rejected():
    with pytest.raises(Error):
        select_severance_tax_policy(
            income_date=date(2026, 10, 20),
            policies=[policy(income_code_4df="101")],
        )


def test_negative_ordinary_income_requires_correction_policy():
    with pytest.raises(Error):
        classify_severance_tax_bases(
            split=income("-100.00", "500.00"),
            income_date=date(2026, 10, 20),
            policies=[policy()],
        )


def test_snapshot_carries_rule_provenance():
    result = classify_severance_tax_bases(
        split=income(),
        income_date=date(2026, 10, 20),
        policies=[policy()],
    )
    assert result.policy_valid_from == date(2026, 8, 1)
    assert result.source_reference
    assert result.policy_code == "UA-ORDINARY-SEVERANCE-2026"


def test_negative_validity_window_is_rejected():
    with pytest.raises(Error):
        select_severance_tax_policy(
            income_date=date(2026, 10, 20),
            policies=[
                policy(valid_to=date(2026, 7, 1)),
            ],
        )
