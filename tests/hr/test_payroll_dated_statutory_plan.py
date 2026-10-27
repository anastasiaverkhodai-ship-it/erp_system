from dataclasses import replace
from decimal import Decimal

import pytest

from app.services.payroll_dated_component_preview import DatedComponentAmount
from app.services.payroll_dated_statutory_plan import (
    PIT, LEVY, USC,
    DatedStatutoryPlanError,
    build_dated_statutory_plan,
)


def row(rate_id, rate, base, source):
    base = Decimal(base)
    rate = Decimal(rate)
    return DatedComponentAmount(
        source_rate_id=rate_id,
        source_profile_id=None,
        source_base_rule_id=None,
        base_amount=base,
        rate=rate,
        amount=(base * rate).quantize(Decimal("0.01")),
        source_line_ids=(source,),
    )


def vector():
    return {
        PIT: (
            row(11, ".18", "600.00", 101),
            row(12, ".19", "400.00", 102),
        ),
        LEVY: (row(21, ".05", "1000.00", 101),),
        USC: (row(31, ".22", "1000.00", 101),),
    }


def plan(components=None, **kwargs):
    return build_dated_statutory_plan(
        gross_amount=kwargs.get("gross_amount", Decimal("1000.00")),
        currency_code=kwargs.get("currency_code", "UAH"),
        components=components if components is not None else vector(),
    )


def test_two_rate_plan_and_reconciliation():
    result = plan()
    assert len(result.lines) == 4
    assert [x.line_no for x in result.lines] == [1, 2, 3, 4]
    assert [x.component for x in result.lines] == [PIT, PIT, LEVY, USC]
    assert [x.source_rate_id for x in result.lines] == [11, 12, 21, 31]
    assert result.employee_withholding_amount == Decimal("234.00")
    assert result.employer_contribution_amount == Decimal("220.00")
    assert result.net_amount == Decimal("766.00")


def test_deterministic_immutable_plan():
    assert plan() == plan()
    assert isinstance(plan().lines, tuple)


@pytest.mark.parametrize(
    "key", [PIT, LEVY, USC]
)
def test_missing_component_rejected(key):
    data = vector()
    del data[key]
    with pytest.raises(DatedStatutoryPlanError):
        plan(data)


def test_invalid_currency_rejected():
    with pytest.raises(DatedStatutoryPlanError, match="UAH"):
        plan(currency_code="EUR")


def test_mismatched_gross_rejected():
    with pytest.raises(DatedStatutoryPlanError, match="reconcile"):
        plan(gross_amount=Decimal("999.00"))


def test_duplicate_salary_source_rejected():
    data = vector()
    data[PIT] = (data[PIT][0], replace(data[PIT][1], source_line_ids=(101,)))
    with pytest.raises(DatedStatutoryPlanError, match="duplicate"):
        plan(data)


def test_profile_policy_rejected():
    data = vector()
    data[PIT] = (
        replace(data[PIT][0], source_profile_id=8),
        data[PIT][1],
    )
    with pytest.raises(DatedStatutoryPlanError, match="Special"):
        plan(data)


def test_base_rule_policy_rejected():
    data = vector()
    data[USC] = (
        replace(data[USC][0], source_base_rule_id=9),
    )
    with pytest.raises(DatedStatutoryPlanError, match="Special"):
        plan(data)


def test_rate_amount_mismatch_rejected():
    data = vector()
    data[PIT] = (
        replace(data[PIT][0], amount=Decimal("1.00")),
        data[PIT][1],
    )
    with pytest.raises(DatedStatutoryPlanError, match="Amount"):
        plan(data)


def test_negative_net_rejected():
    data = vector()
    data[PIT] = (
        row(11, "1", "600.00", 101),
        row(12, "1", "400.00", 102),
    )
    data[LEVY] = (row(21, "1", "1000.00", 101),)
    with pytest.raises(DatedStatutoryPlanError, match="exceeds"):
        plan(data)


def test_repeated_rate_rejected():
    data = vector()
    data[PIT] = (
        data[PIT][0],
        replace(data[PIT][1], source_rate_id=11),
    )
    with pytest.raises(DatedStatutoryPlanError, match="rate provenance"):
        plan(data)
