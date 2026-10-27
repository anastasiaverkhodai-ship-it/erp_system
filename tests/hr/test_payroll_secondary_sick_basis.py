from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.services.payroll_sick_history_service import (
    _validate_joint_imported_sick_months,
    PayrollSickLeaveDerivationError,
)


def target(amount="10000"):
    return [{
        "source_type": "earnings_history",
        "source_period_start": date(2026, 7, 1),
        "source_period_end": date(2026, 7, 31),
        "earnings_amount": D(amount),
        "eligible_days": D(31),
    }]


def other(amount="20000", contract_id=2):
    return SimpleNamespace(
        employment_contract_id=contract_id,
        month=date(2026, 7, 1),
        sick_earnings=D(amount),
        sick_days=D(31),
        cutover_date=date(2026, 8, 1),
        source_reference="Reviewed imported insurance history",
    )


def test_internal_secondary_joint_earnings_verified():
    result = _validate_joint_imported_sick_months(
        target(), [other()], [2]
    )
    assert result[0]["combined_earnings"] == "30000"
    assert result[0]["contract_count"] == 2


@pytest.mark.parametrize("rows,ids", [
    ([], [2]),
    ([other(), other()], [2]),
    ([other(contract_id=3)], [2]),
    ([other()], [2, 3]),
    ([other()], []),
])
def test_missing_duplicate_or_unattributed_contracts_rejected(rows, ids):
    with pytest.raises(PayrollSickLeaveDerivationError):
        _validate_joint_imported_sick_months(
            target(), rows, ids
        )


def test_combined_monthly_ceiling_rejected():
    with pytest.raises(
        PayrollSickLeaveDerivationError,
        match="USC maximum",
    ):
        _validate_joint_imported_sick_months(
            target("120000"), [other("120000")], [2]
        )


def test_non_history_target_rejected():
    rows = target()
    rows[0]["source_type"] = "erp_calculation"
    with pytest.raises(
        PayrollSickLeaveDerivationError,
        match="fully imported",
    ):
        _validate_joint_imported_sick_months(
            rows, [other()], [2]
        )


def test_unverified_other_source_rejected():
    record = other()
    record.source_reference = " "
    with pytest.raises(
        PayrollSickLeaveDerivationError,
        match="Unverified",
    ):
        _validate_joint_imported_sick_months(
            target(), [record], [2]
        )
