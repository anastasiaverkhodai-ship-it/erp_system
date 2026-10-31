from datetime import date

import pytest

from app.services.payroll_regulatory_d1_dated_sources import (
    D1DatedSourceError,
    dated_source_control,
    decimal_amount,
)


def test_valid_dated_source():
    result = dated_source_control(
        statutory_line_id=1,
        statutory_amount="22.00",
        source_records=[{
            "source_id": 10,
            "payroll_calculation_line_id": 5,
            "effective_from": date(2026, 10, 1),
            "effective_to": date(2026, 10, 31),
            "source_amount": "100.00",
        }],
    )

    assert result["dated_source_coverage"] == "present"
    assert result["dated_source_amount_total"] == "100.00"
    assert result["statutory_contribution_amount"] == "22.00"


def test_missing_provenance_is_not_silently_approved():
    result = dated_source_control(
        statutory_line_id=1,
        statutory_amount="22.00",
        source_records=[],
    )

    assert result["dated_source_coverage"] == "missing"


@pytest.mark.parametrize(
    "value",
    [None, True, "NaN", "Infinity", "bad", "1.001"],
)
def test_invalid_amount_rejected(value):
    with pytest.raises(D1DatedSourceError):
        decimal_amount(value)


def test_invalid_interval_rejected():
    with pytest.raises(
        D1DatedSourceError,
        match="interval",
    ):
        dated_source_control(
            statutory_line_id=1,
            statutory_amount="22.00",
            source_records=[{
                "source_id": 1,
                "payroll_calculation_line_id": 5,
                "effective_from": date(2026, 10, 31),
                "effective_to": date(2026, 10, 1),
                "source_amount": "100.00",
            }],
        )


def test_duplicate_dated_source_rejected():
    item = {
        "source_id": 1,
        "payroll_calculation_line_id": 5,
        "effective_from": date(2026, 10, 1),
        "effective_to": date(2026, 10, 31),
        "source_amount": "100.00",
    }

    with pytest.raises(
        D1DatedSourceError,
        match="Duplicate",
    ):
        dated_source_control(
            statutory_line_id=1,
            statutory_amount="22.00",
            source_records=[item, item],
        )
