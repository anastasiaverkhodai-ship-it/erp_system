from datetime import date
from decimal import Decimal

import pytest

from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
    calculate_severance_average,
)


def evidence(**changes):
    data = dict(
        reference_start=date(2026, 7, 1),
        reference_end=date(2026, 8, 31),
        eligible_earnings=Decimal("24000.00"),
        worked_days=Decimal("40"),
        scheduled_days=Decimal("44"),
        reference_months=2,
        source_reference="Verified payroll history",
    )
    data.update(changes)
    return data


def test_positive_average():
    result = calculate_severance_average(**evidence())
    assert result.average_daily == Decimal("600")
    assert result.average_monthly == Decimal("13200.00")
    assert result.rule_code == "UA_ORDER_100_SEVERANCE_2_MONTH"


@pytest.mark.parametrize("changes", [
    {"worked_days": Decimal("0")},
    {"worked_days": Decimal("-1")},
    {"scheduled_days": Decimal("0")},
    {"worked_days": Decimal("50")},
    {"eligible_earnings": Decimal("-100")},
    {"eligible_earnings": Decimal("0")},
    {"eligible_earnings": Decimal("12.345")},
    {"eligible_earnings": float("nan")},
    {"reference_months": 12},
    {"reference_months": True},
    {"source_reference": ""},
    {"reference_start": date(2026, 9, 1)},
    {"worked_days": Decimal("NaN")},
])
def test_bad_evidence_fails_closed(changes):
    with pytest.raises(SeveranceAverageError):
        calculate_severance_average(**evidence(**changes))


def test_amount_is_not_silently_rounded():
    result = calculate_severance_average(
        **evidence(
            eligible_earnings=Decimal("10000.01"),
            worked_days=Decimal("17"),
            scheduled_days=Decimal("40"),
        )
    )
    expected = (
        Decimal("10000.01") / Decimal("17") * Decimal("20")
    ).quantize(Decimal(".01"))
    assert result.average_monthly == expected
