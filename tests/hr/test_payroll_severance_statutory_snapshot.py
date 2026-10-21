"""Immutable severance statutory snapshot contract."""

from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal

import pytest

from app.services.payroll_severance_income_classification import (
    PayrollIncomeSplit,
)
from app.services.payroll_severance_tax_classification import (
    SeveranceTaxBaseSnapshot,
)
from app.services.payroll_severance_statutory_snapshot import (
    SeveranceSnapshotError,
    build_severance_statutory_snapshot,
    verify_severance_statutory_snapshot,
)


DAY = date(2026, 10, 20)


def evidence():
    split = PayrollIncomeSplit(
        ordinary_gross=Decimal("10000.00"),
        severance_gross=Decimal("5000.00"),
        total_gross=Decimal("15000.00"),
        severance_source_ids=(41,),
    )
    tax = SeveranceTaxBaseSnapshot(
        policy_code="TEST-ORDINARY-SEVERANCE",
        policy_valid_from=date(2026, 10, 1),
        policy_valid_to=None,
        source_reference="TEST-ONLY-EVIDENCE",
        income_code_4df="127",
        personal_income_tax_base=Decimal("15000.00"),
        military_levy_base=Decimal("15000.00"),
        unified_social_contribution_base=Decimal("10000.00"),
        ordinary_gross=Decimal("10000.00"),
        severance_gross=Decimal("5000.00"),
    )
    return split, tax


def make(**changes):
    split, tax = evidence()
    values = dict(
        company_id=1,
        payroll_calculation_id=2,
        income_date=DAY,
        income_split=split,
        tax_bases=tax,
    )
    values.update(changes)
    return build_severance_statutory_snapshot(**values)


def test_snapshot_has_expected_component_bases():
    result = make()
    assert result.personal_income_tax_base == Decimal("15000.00")
    assert result.military_levy_base == Decimal("15000.00")
    assert result.unified_social_contribution_base == Decimal("10000.00")
    assert verify_severance_statutory_snapshot(result)


def test_same_evidence_produces_same_fingerprint():
    assert make().fingerprint_sha256 == make().fingerprint_sha256


def test_snapshot_is_frozen():
    with pytest.raises(FrozenInstanceError):
        make().severance_gross = Decimal("0.00")


def test_mutated_snapshot_fails_integrity_check():
    changed = replace(
        make(),
        severance_gross=Decimal("9000.00"),
    )
    with pytest.raises(SeveranceSnapshotError):
        verify_severance_statutory_snapshot(changed)


def test_tax_base_mismatch_is_rejected():
    split, tax = evidence()
    changed = replace(
        tax, severance_gross=Decimal("1.00")
    )
    with pytest.raises(SeveranceSnapshotError):
        make(income_split=split, tax_bases=changed)


def test_policy_outside_income_date_is_rejected():
    split, tax = evidence()
    changed = replace(
        tax, policy_valid_to=date(2026, 10, 10)
    )
    with pytest.raises(SeveranceSnapshotError):
        make(income_split=split, tax_bases=changed)


def test_undocumented_source_is_rejected():
    split, tax = evidence()
    changed = replace(split, severance_source_ids=())
    with pytest.raises(SeveranceSnapshotError):
        make(income_split=changed, tax_bases=tax)


def test_wrong_currency_is_rejected():
    with pytest.raises(SeveranceSnapshotError):
        make(currency_code="EUR")


def test_invalid_company_id_is_rejected():
    with pytest.raises(SeveranceSnapshotError):
        make(company_id=0)
