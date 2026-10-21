"""Read-only severance integration tests. No database required."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import (
    payroll_severance_snapshot_integration_service as integration,
)
from app.services.payroll_severance_tax_classification import (
    SeveranceTaxPolicy,
    SeveranceTaxClassificationError,
)
from app.services.payroll_severance_statutory_snapshot import (
    verify_severance_statutory_snapshot,
)


DAY = date(2026, 9, 30)


def calculation(company_id=1, calc_id=10):
    return SimpleNamespace(
        company_id=company_id,
        id=calc_id,
        currency_code="UAH",
        gross_amount=Decimal("13040.00"),
    )


def lines():
    return [
        SimpleNamespace(
            line_type="salary",
            amount=Decimal("400.00"),
            currency_code="UAH",
            source_termination_severance_id=None,
        ),
        SimpleNamespace(
            line_type="minimum_wage",
            amount=Decimal("640.00"),
            currency_code="UAH",
            source_termination_severance_id=None,
        ),
        SimpleNamespace(
            line_type="severance",
            amount=Decimal("12000.00"),
            currency_code="UAH",
            source_termination_severance_id=41,
        ),
    ]


def make_test_policy():
    # Deliberately synthetic test fixture.
    # This is NOT a legally approved Ukrainian tax rule.
    return SeveranceTaxPolicy(
        code="TEST-SEVERANCE-INTEGRATION",
        valid_from=date(2026, 9, 1),
        valid_to=date(2026, 9, 30),
        source_reference="TEST-ONLY-NOT-LEGAL-APPROVAL",
        personal_income_tax_included=True,
        military_levy_included=True,
        unified_social_contribution_included=False,
        income_code_4df="127",
    )


@pytest.fixture
def mocked_services(monkeypatch):
    calls = []

    async def get_calc(session, *, company_id, payroll_calculation_id):
        calls.append(("calculation", company_id, payroll_calculation_id))
        return calculation(company_id, payroll_calculation_id)

    async def get_lines(session, *, company_id, payroll_calculation_id):
        calls.append(("lines", company_id, payroll_calculation_id))
        return lines()

    monkeypatch.setattr(
        integration,
        "get_payroll_calculation",
        get_calc,
    )

    monkeypatch.setattr(
        integration,
        "list_payroll_calculation_lines",
        get_lines,
    )

    return calls


@pytest.mark.asyncio
async def test_positive_preview_uses_actual_payroll_lines(mocked_services):
    result = await integration.preview_severance_statutory_snapshot(
        object(),
        company_id=1,
        payroll_calculation_id=10,
        income_date=DAY,
        policies=[make_test_policy()],
    )

    assert result.company_id == 1
    assert result.payroll_calculation_id == 10
    assert result.ordinary_gross == Decimal("1040.00")
    assert result.severance_gross == Decimal("12000.00")
    assert result.total_gross == Decimal("13040.00")
    assert result.severance_source_ids == (41,)
    assert verify_severance_statutory_snapshot(result)

    assert mocked_services == [
        ("calculation", 1, 10),
        ("lines", 1, 10),
    ]


@pytest.mark.asyncio
async def test_missing_policy_fails_before_database_access(mocked_services):
    with pytest.raises(
        integration.SeveranceSnapshotIntegrationError,
        match="Explicit dated",
    ):
        await integration.preview_severance_statutory_snapshot(
            object(),
            company_id=1,
            payroll_calculation_id=10,
            income_date=DAY,
            policies=None,
        )

    assert mocked_services == []


@pytest.mark.asyncio
async def test_empty_policy_list_fails_closed(mocked_services):
    with pytest.raises(
        SeveranceTaxClassificationError,
        match="Exactly one",
    ):
        await integration.preview_severance_statutory_snapshot(
            object(),
            company_id=1,
            payroll_calculation_id=10,
            income_date=DAY,
            policies=[],
        )


@pytest.mark.asyncio
async def test_ambiguous_policies_fail_closed(mocked_services):
    with pytest.raises(
        SeveranceTaxClassificationError,
        match="Exactly one",
    ):
        await integration.preview_severance_statutory_snapshot(
            object(),
            company_id=1,
            payroll_calculation_id=10,
            income_date=DAY,
            policies=[make_test_policy(), make_test_policy()],
        )


@pytest.mark.asyncio
async def test_no_fallback_for_expired_policy(mocked_services):
    with pytest.raises(
        SeveranceTaxClassificationError,
        match="Exactly one",
    ):
        await integration.preview_severance_statutory_snapshot(
            object(),
            company_id=1,
            payroll_calculation_id=10,
            income_date=date(2026, 10, 1),
            policies=[make_test_policy()],
        )


@pytest.mark.asyncio
async def test_invalid_company_is_rejected_before_read(mocked_services):
    with pytest.raises(
        integration.SeveranceSnapshotIntegrationError,
        match="company_id",
    ):
        await integration.preview_severance_statutory_snapshot(
            object(),
            company_id=-1,
            payroll_calculation_id=10,
            income_date=DAY,
            policies=[make_test_policy()],
        )

    assert mocked_services == []


@pytest.mark.asyncio
async def test_invalid_income_date_is_rejected(mocked_services):
    with pytest.raises(
        integration.SeveranceSnapshotIntegrationError,
        match="income_date",
    ):
        await integration.preview_severance_statutory_snapshot(
            object(),
            company_id=1,
            payroll_calculation_id=10,
            income_date="2026-09-30",
            policies=[make_test_policy()],
        )

    assert mocked_services == []


@pytest.mark.asyncio
async def test_no_severance_source_is_rejected(
    monkeypatch, mocked_services
):
    async def without_severance(
        session, *, company_id, payroll_calculation_id
    ):
        return [
            SimpleNamespace(
                line_type="salary",
                amount=Decimal("13040.00"),
                currency_code="UAH",
                source_termination_severance_id=None,
            )
        ]

    monkeypatch.setattr(
        integration,
        "list_payroll_calculation_lines",
        without_severance,
    )

    with pytest.raises(
        integration.SeveranceSnapshotIntegrationError,
        match="no documented severance",
    ):
        await integration.preview_severance_statutory_snapshot(
            object(),
            company_id=1,
            payroll_calculation_id=10,
            income_date=DAY,
            policies=[make_test_policy()],
        )


@pytest.mark.asyncio
async def test_cross_company_calculation_is_rejected(
    monkeypatch, mocked_services
):
    async def wrong_company(
        session, *, company_id, payroll_calculation_id
    ):
        return calculation(company_id=99, calc_id=payroll_calculation_id)

    monkeypatch.setattr(
        integration,
        "get_payroll_calculation",
        wrong_company,
    )

    with pytest.raises(
        integration.SeveranceSnapshotIntegrationError,
        match="ownership mismatch",
    ):
        await integration.preview_severance_statutory_snapshot(
            object(),
            company_id=1,
            payroll_calculation_id=10,
            income_date=DAY,
            policies=[make_test_policy()],
        )

    assert not any(
        entry[0] == "lines" for entry in mocked_services
    )


def test_integration_never_modifies_financial_guard():
    from pathlib import Path

    source = Path(
        "app/services/payroll_severance_snapshot_integration_service.py"
    ).read_text(encoding="utf-8")

    assert "calculate_payroll_statutory_result(" not in source
    assert "calculate_payroll_deduction_result(" not in source
    assert "require_severance_financial_classification =" not in source
    assert "session.add(" not in source
    assert "session.commit(" not in source
    assert "session.flush(" not in source
    assert "db.add(" not in source
