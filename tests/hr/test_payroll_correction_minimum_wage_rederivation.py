"""Unit contracts for independent minimum-wage re-derivation."""

from types import SimpleNamespace as Row
from unittest.mock import AsyncMock, patch

import pytest

from app.services.payroll_correction_minimum_wage_rederivation import (
    PayrollMinimumWageRederivationError,
    verify_original_minimum_wage_rederivation,
)


def _case():
    from datetime import date
    from decimal import Decimal

    period = Row(
        id=10,
        company_id=1,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    )

    payroll_input = Row(
        id=20,
        company_id=1,
        payroll_period_id=10,
        employment_contract_id=30,
    )

    salary_slices = [
        Row(
            company_id=1,
            payroll_input_id=20,
            salary_rate_type="hourly",
        )
    ]

    assessment = {
        "status": "assessed",
        "rule_version": "UA-4695-IX-2026-v1",
        "source_reference":
            "https://zakon.rada.gov.ua/laws/show/4695-IX",
        "rate_type": "hourly",
        "minimum_rate": "52",
        "ordinary_worked_minutes": 6000,
        "full_time_norm_minutes": None,
        "guaranteed_amount": "5200.00",
        "included_earnings": "4000.00",
        "excluded_earnings": "0.00",
        "top_up": "1200.00",
        "request": {},
    }

    original = Row(
        id=40,
        company_id=1,
        payroll_input_id=20,
        payroll_period_id=10,
        employment_contract_id=30,
        currency_code="UAH",
        gross_amount=Decimal("5200.00"),
        minimum_wage_assessment=assessment,
    )

    lines = [
        {
            "company_id": 1,
            "payroll_calculation_id": 40,
            "line_type": "salary",
            "currency_code": "UAH",
            "amount": Decimal("4000.00"),
        },
        {
            "company_id": 1,
            "payroll_calculation_id": 40,
            "line_type": "minimum_wage",
            "currency_code": "UAH",
            "quantity": Decimal("1"),
            "rate": Decimal("1200.00"),
            "amount": Decimal("1200.00"),
            "source_effective_from": period.start_date,
            "source_effective_to": period.end_date,
        },
    ]

    return dict(
        db=object(),
        company_id=1,
        period=period,
        payroll_input=payroll_input,
        salary_slices=salary_slices,
        original_calculation=original,
        original_lines=lines,
    )


def _derived_line(case):
    return {
        "line_type": "minimum_wage",
        "currency_code": "UAH",
        "quantity": 1,
        "rate": "1200.00",
        "amount": "1200.00",
        "source_effective_from": case["period"].start_date,
        "source_effective_to": case["period"].end_date,
    }


@pytest.mark.asyncio
async def test_verified_current_rederivation_still_blocks_revision():
    case = _case()

    with patch(
        "app.services."
        "payroll_correction_minimum_wage_rederivation."
        "derive_minimum_wage_assessment",
        new_callable=AsyncMock,
    ) as derive:
        derive.return_value = (
            case["original_calculation"].minimum_wage_assessment,
            _derived_line(case),
        )

        result = await verify_original_minimum_wage_rederivation(
            **case
        )

        assert derive.await_count == 1

    assert result["current_erp_rederivation_reconciled"] is True
    assert result["complete_payroll_provenance_verified"] is False
    assert result["automatic_revision_creation_allowed"] is False
    assert result["financial_execution_allowed"] is False


@pytest.mark.asyncio
async def test_changed_assessment_is_rejected():
    case = _case()
    changed = dict(
        case["original_calculation"].minimum_wage_assessment
    )
    changed["included_earnings"] = "3999.00"

    with patch(
        "app.services."
        "payroll_correction_minimum_wage_rederivation."
        "derive_minimum_wage_assessment",
        new_callable=AsyncMock,
    ) as derive:
        derive.return_value = (changed, _derived_line(case))

        with pytest.raises(
            PayrollMinimumWageRederivationError,
            match="differs",
        ):
            await verify_original_minimum_wage_rederivation(
                **case
            )


@pytest.mark.asyncio
async def test_modified_gross_is_rejected_before_derivation():
    case = _case()
    case["original_calculation"].gross_amount = 5300

    with pytest.raises(
        PayrollMinimumWageRederivationError,
        match="gross reconciliation",
    ):
        await verify_original_minimum_wage_rederivation(**case)


@pytest.mark.asyncio
async def test_wrong_source_company_is_rejected():
    case = _case()
    case["salary_slices"][0].company_id = 2

    with pytest.raises(
        PayrollMinimumWageRederivationError,
        match="Salary slice identity",
    ):
        await verify_original_minimum_wage_rederivation(**case)


@pytest.mark.asyncio
async def test_changed_top_up_is_rejected():
    case = _case()
    changed_line = dict(_derived_line(case))
    changed_line["amount"] = "1100.00"

    with patch(
        "app.services."
        "payroll_correction_minimum_wage_rederivation."
        "derive_minimum_wage_assessment",
        new_callable=AsyncMock,
    ) as derive:
        derive.return_value = (
            case["original_calculation"].minimum_wage_assessment,
            changed_line,
        )

        with pytest.raises(
            PayrollMinimumWageRederivationError,
            match="top-up differs",
        ):
            await verify_original_minimum_wage_rederivation(
                **case
            )
