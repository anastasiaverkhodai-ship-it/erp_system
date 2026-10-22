from datetime import date
from decimal import Decimal
import pytest
from app.services.payroll_severance_trusted_classification import TRUSTED_POLICIES
from app.services.payroll_severance_tax_classification import (
    classify_severance_tax_bases, select_severance_tax_policy, SeveranceTaxClassificationError,
)
from app.services.payroll_severance_income_classification import PayrollIncomeSplit

@pytest.mark.parametrize('day', [date(2026,1,1), date(2026,9,30), date(2026,10,21), date(2026,10,22), date(2026,12,31)])
def test_reviewed_window_and_exact_bases(day):
    result = classify_severance_tax_bases(
        split=PayrollIncomeSplit(Decimal('1040.00'), Decimal('12000.00'), Decimal('13040.00'), (1,)),
        income_date=day, policies=TRUSTED_POLICIES,
    )
    assert result.personal_income_tax_base == Decimal('13040.00')
    assert result.military_levy_base == Decimal('13040.00')
    assert result.unified_social_contribution_base == Decimal('1040.00')
    assert result.income_code_4df == '127'

@pytest.mark.parametrize('day', [date(2025,12,31)])
def test_unreviewed_dates_fail_closed(day):
    with pytest.raises(SeveranceTaxClassificationError, match='Exactly one'):
        select_severance_tax_policy(income_date=day, policies=TRUSTED_POLICIES)

def test_ambiguous_registry_fails_closed():
    with pytest.raises(SeveranceTaxClassificationError, match='Exactly one'):
        select_severance_tax_policy(income_date=date(2026,9,30), policies=TRUSTED_POLICIES * 2)


def test_current_version_preserves_historical_snapshot_policy():
    old = select_severance_tax_policy(income_date=date(2026,10,21), policies=TRUSTED_POLICIES)
    current = select_severance_tax_policy(income_date=date(2026,10,22), policies=TRUSTED_POLICIES)
    assert old.code == 'UA-ORDINARY-TERMINATION-2026-v1'
    assert old.valid_to == date(2026,10,21)
    assert current.code == 'UA-ORDINARY-TERMINATION-2026-v2'
    assert current.valid_to is None
