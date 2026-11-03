from decimal import Decimal
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from app.services.payroll_severance_corrected_source_reconciliation import (
    CorrectedSeveranceSourceError,
    load_corrected_severance_source_reconciliation,
    reconcile_corrected_severance_sources,
)


def fixture():
    case = Obj(
        id=10,
        company_id=1,
        payroll_input_id=31,
        original_severance_source_id=100,
        original_severance_gross=Decimal("1000.00"),
        status="review_required",
    )

    proposal = Obj(
        id=20,
        company_id=1,
        correction_case_id=10,
        status="review_required",
        original_financial={"gross": "1000.00"},
        proposed_financial={"gross": "1200.00"},
    )

    first = Obj(
        id=100,
        company_id=1,
        payroll_input_id=31,
        termination_event_id=70,
        revision=1,
        supersedes_id=None,
        gross_amount=Decimal("1000.00"),
    )

    latest = Obj(
        id=101,
        company_id=1,
        payroll_input_id=31,
        termination_event_id=70,
        revision=2,
        supersedes_id=100,
        gross_amount=Decimal("1200.00"),
    )

    return case, proposal, [first, latest]


def reconcile(rows=None):
    case, proposal, sources = rows or fixture()
    return reconcile_corrected_severance_sources(
        company_id=1,
        proposal=proposal,
        correction_case=case,
        sources=sources,
    )


def test_valid_revision_chain_reconciles_gross():
    result = reconcile()
    assert result.original_source_id == 100
    assert result.corrected_source_id == 101
    assert result.corrected_revision == 2
    assert result.corrected_gross == Decimal("1200.00")
    assert result.source_chain_verified
    assert result.proposed_gross_reconciled
    assert not result.corrected_statutory_verified
    assert not result.posting_authorized


def test_three_revisions_latest_must_match():
    case, proposal, sources = fixture()
    sources.append(
        Obj(
            id=102,
            company_id=1,
            payroll_input_id=31,
            termination_event_id=70,
            revision=3,
            supersedes_id=101,
            gross_amount=Decimal("1400.00"),
        )
    )
    proposal.proposed_financial = {"gross": "1400.00"}
    result = reconcile((case, proposal, sources))
    assert result.corrected_revision == 3


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_original",
        "missing_revision",
        "broken_supersedes",
        "wrong_company",
        "wrong_input",
        "wrong_event",
        "wrong_original_gross",
        "wrong_proposed_gross",
        "same_proposed_gross",
        "duplicate_revision",
        "duplicate_source",
        "invalid_amount",
        "invalid_json",
        "invalid_precision",
        "missing_sources",
    ],
)
def test_invalid_source_or_proposal_rejected(mutation):
    case, proposal, sources = fixture()

    if mutation == "missing_original":
        case.original_severance_source_id = 999
    elif mutation == "missing_revision":
        sources[1].revision = 3
    elif mutation == "broken_supersedes":
        sources[1].supersedes_id = 999
    elif mutation == "wrong_company":
        sources[1].company_id = 2
    elif mutation == "wrong_input":
        sources[1].payroll_input_id = 32
    elif mutation == "wrong_event":
        sources[1].termination_event_id = 71
    elif mutation == "wrong_original_gross":
        proposal.original_financial["gross"] = "999.00"
    elif mutation == "wrong_proposed_gross":
        proposal.proposed_financial["gross"] = "1199.00"
    elif mutation == "same_proposed_gross":
        proposal.proposed_financial["gross"] = "1000.00"
    elif mutation == "duplicate_revision":
        sources[1].revision = 1
    elif mutation == "duplicate_source":
        sources[1].id = 100
    elif mutation == "invalid_amount":
        proposal.proposed_financial["gross"] = "NaN"
    elif mutation == "invalid_json":
        proposal.proposed_financial = []
    elif mutation == "invalid_precision":
        proposal.proposed_financial["gross"] = "1200.001"
    elif mutation == "missing_sources":
        sources.clear()

    with pytest.raises(CorrectedSeveranceSourceError):
        reconcile((case, proposal, sources))


@pytest.mark.parametrize(
    "company_id",
    [0, -1, True, "1"],
)
def test_invalid_company_rejected(company_id):
    case, proposal, sources = fixture()

    with pytest.raises(CorrectedSeveranceSourceError):
        reconcile_corrected_severance_sources(
            company_id=company_id,
            proposal=proposal,
            correction_case=case,
            sources=sources,
        )


@pytest.mark.asyncio
async def test_missing_proposal_rejected():
    db = Obj(scalar=AsyncMock(return_value=None))

    with pytest.raises(
        CorrectedSeveranceSourceError,
        match="Financial proposal not found",
    ):
        await load_corrected_severance_source_reconciliation(
            db,
            company_id=1,
            financial_proposal_id=20,
        )


@pytest.mark.asyncio
async def test_invalid_load_ids_do_not_query():
    db = Obj(scalar=AsyncMock())

    with pytest.raises(CorrectedSeveranceSourceError):
        await load_corrected_severance_source_reconciliation(
            db,
            company_id=True,
            financial_proposal_id=20,
        )

    db.scalar.assert_not_awaited()
