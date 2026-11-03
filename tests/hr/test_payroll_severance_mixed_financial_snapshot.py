from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.payroll_severance_dated_tax_allocation import (
    ComponentAllocation,
    build_dated_severance_allocation_review,
)
from app.services.payroll_severance_mixed_financial_snapshot import (
    SeveranceFinancialSourceError,
    bind_review_to_statutory_totals,
    load_mixed_severance_financial_preview,
)


TOTALS = {
    "personal_income_tax": D("2347.20"),
    "military_levy": D("652.00"),
    "unified_social_contribution": D("228.80"),
}


def review():
    values = {
        "personal_income_tax": D("1800.00"),
        "military_levy": D("500.00"),
        "unified_social_contribution": D("0.00"),
    }
    return build_dated_severance_allocation_review(
        company_id=1,
        payroll_calculation_id=2,
        income_date=date(2026, 10, 30),
        currency_code="UAH",
        rows=[
            ComponentAllocation(
                component=name,
                statutory_total=total,
                ordinary_amount=total - values[name],
                severance_amount=values[name],
                evidence_reference="TEST-ONLY-NOT-APPROVED",
            )
            for name, total in TOTALS.items()
        ],
    )


def bind(item=None, totals=None, sources=(15,)):
    return bind_review_to_statutory_totals(
        review=review() if item is None else item,
        company_id=1,
        calculation_id=2,
        statutory_amounts=TOTALS if totals is None else totals,
        severance_gross=D("10000.00"),
        ordinary_gross=D("3040.00"),
        source_ids=sources,
    )


def test_real_totals_can_produce_review_only_snapshot():
    result = bind()

    assert result.financial.gross == D("10000.00")
    assert result.financial.net == D("7700.00")
    assert result.financial.personal_income_tax == D("1800.00")
    assert result.financial.military_levy == D("500.00")
    assert result.financial.employer_contribution == D("0.00")
    assert result.ordinary_gross == D("3040.00")
    assert result.statutory_totals_reconciled

    assert not result.allocation_evidence_approved
    assert not result.payment_authorized
    assert not result.tax_correction_authorized
    assert not result.journal_posting_authorized


def test_tampered_postgresql_total_rejected():
    totals = dict(TOTALS)
    totals["personal_income_tax"] += D("0.01")

    with pytest.raises(
        SeveranceFinancialSourceError,
        match="Persisted statutory mismatch",
    ):
        bind(totals=totals)


def test_wrong_calculation_identity_rejected():
    bad = replace(review(), payroll_calculation_id=999)

    with pytest.raises(
        SeveranceFinancialSourceError,
        match="does not belong",
    ):
        bind(item=bad)


def test_evidence_flags_cannot_be_self_approved():
    bad = replace(
        review(),
        independent_evidence_approved=True,
    )

    with pytest.raises(
        SeveranceFinancialSourceError,
        match="Only unapproved",
    ):
        bind(item=bad)


@pytest.mark.parametrize(
    "sources",
    [(), (0,), (True,), (15, 15), (-1,)],
)
def test_invalid_severance_source_ids_rejected(sources):
    with pytest.raises(SeveranceFinancialSourceError):
        bind(sources=sources)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id,calculation_id",
    [(0, 2), (1, 0), (True, 2), (1, False)],
)
async def test_bad_identity_stops_before_database(
    company_id,
    calculation_id,
):
    db = MagicMock()
    db.scalar = AsyncMock()
    db.scalars = AsyncMock()

    with pytest.raises(SeveranceFinancialSourceError):
        await load_mixed_severance_financial_preview(
            db,
            company_id=company_id,
            payroll_calculation_id=calculation_id,
            review=review(),
        )

    db.scalar.assert_not_awaited()
    db.scalars.assert_not_awaited()


def test_statutory_header_full_reconciliation():
    from types import SimpleNamespace
    from app.services.payroll_severance_mixed_financial_snapshot import (
        verify_statutory_financial_integrity,
    )

    header = SimpleNamespace(
        gross_amount=D("13040.00"),
        employee_withholding_amount=D("2999.20"),
        employer_contribution_amount=D("228.80"),
        net_amount=D("10040.80"),
    )

    assert verify_statutory_financial_integrity(
        gross_amount=D("13040.00"),
        statutory_header=header,
        component_totals=TOTALS,
    )


@pytest.mark.parametrize(
    "field,incorrect",
    [
        ("gross_amount", D("13040.01")),
        ("employee_withholding_amount", D("2999.21")),
        ("employer_contribution_amount", D("228.81")),
        ("net_amount", D("10040.81")),
    ],
)
def test_statutory_header_tampering_rejected(field, incorrect):
    from types import SimpleNamespace
    from app.services.payroll_severance_mixed_financial_snapshot import (
        verify_statutory_financial_integrity,
    )

    values = dict(
        gross_amount=D("13040.00"),
        employee_withholding_amount=D("2999.20"),
        employer_contribution_amount=D("228.80"),
        net_amount=D("10040.80"),
    )
    values[field] = incorrect

    with pytest.raises(SeveranceFinancialSourceError):
        verify_statutory_financial_integrity(
            gross_amount=D("13040.00"),
            statutory_header=SimpleNamespace(**values),
            component_totals=TOTALS,
        )
