from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from app.services.payroll_severance_execution_readiness import (
    SeveranceExecutionReadinessError,
    assess_severance_execution_readiness,
    evaluate_severance_execution_readiness,
)


def evidence():
    proposal = Obj(
        id=20,
        company_id=1,
        correction_case_id=10,
        submitted_by=7,
        status="review_required",
    )
    case = Obj(
        id=10,
        company_id=1,
        original_payroll_calculation_id=30,
        status="review_required",
    )
    review = Obj(
        company_id=1,
        financial_proposal_id=20,
        reviewer_id=8,
        decision="reviewed_pending_statutory",
    )
    link = Obj(
        company_id=1,
        financial_proposal_id=20,
        journal_entry_id=40,
    )
    journal = Obj(
        id=40,
        company_id=1,
        status="draft",
    )
    return proposal, case, review, link, journal


def evaluate(rows=None, *, verified=True):
    proposal, case, review, link, journal = rows or evidence()
    return evaluate_severance_execution_readiness(
        company_id=1,
        financial_proposal_id=20,
        proposal=proposal,
        correction_case=case,
        independent_review=review,
        adjustment_link=link,
        journal=journal,
        original_source_verified=verified,
    )


def test_complete_existing_documents_are_still_not_statutory_approval():
    result = evaluate()
    assert result.original_source_verified
    assert result.independent_review_verified
    assert result.draft_journal_verified
    assert not result.corrected_statutory_source_verified
    assert not result.posting_authorized
    assert result.blockers == (
        "corrected_trusted_statutory_source_required",
    )


@pytest.mark.parametrize(
    "field",
    ["proposal", "case", "review", "link", "journal"],
)
def test_missing_document_blocks_execution(field):
    rows = list(evidence())
    index = {
        "proposal": 0,
        "case": 1,
        "review": 2,
        "link": 3,
        "journal": 4,
    }[field]
    rows[index] = None
    result = evaluate(rows=rows)
    assert not result.posting_authorized
    assert len(result.blockers) >= 2


def test_rejected_review_blocks():
    rows = list(evidence())
    rows[2].decision = "rejected"
    result = evaluate(rows=rows)
    assert not result.independent_review_verified
    assert "independent_review_missing_or_rejected" in result.blockers


def test_self_review_blocks():
    rows = list(evidence())
    rows[2].reviewer_id = 7
    assert not evaluate(rows=rows).independent_review_verified


def test_foreign_company_document_blocks():
    rows = list(evidence())
    rows[3].company_id = 2
    result = evaluate(rows=rows)
    assert not result.draft_journal_verified


@pytest.mark.parametrize("status", ["posted", "reversed", "void"])
def test_non_draft_journal_blocks(status):
    rows = list(evidence())
    rows[4].status = status
    assert not evaluate(rows=rows).draft_journal_verified


def test_missing_trusted_original_blocks():
    result = evaluate(verified=False)
    assert not result.original_source_verified
    assert "original_trusted_statutory_snapshot_not_verified" in (
        result.blockers
    )


@pytest.mark.parametrize(
    "company_id,proposal_id",
    [(0, 20), (1, 0), (True, 20), (1, True), (-1, 20)],
)
def test_invalid_identifiers_rejected(company_id, proposal_id):
    rows = evidence()
    with pytest.raises(SeveranceExecutionReadinessError):
        evaluate_severance_execution_readiness(
            company_id=company_id,
            financial_proposal_id=proposal_id,
            proposal=rows[0],
            correction_case=rows[1],
            independent_review=rows[2],
            adjustment_link=rows[3],
            journal=rows[4],
            original_source_verified=True,
        )


@pytest.mark.asyncio
async def test_missing_database_rows_fail_closed():
    db = Obj(scalar=AsyncMock(return_value=None))
    result = await assess_severance_execution_readiness(
        db,
        company_id=1,
        financial_proposal_id=20,
    )
    assert not result.posting_authorized
    assert "financial_proposal_missing_or_invalid" in result.blockers
    assert "corrected_trusted_statutory_source_required" in result.blockers


@pytest.mark.asyncio
async def test_invalid_identifiers_do_not_query_database():
    db = Obj(scalar=AsyncMock())
    with pytest.raises(SeveranceExecutionReadinessError):
        await assess_severance_execution_readiness(
            db,
            company_id=True,
            financial_proposal_id=20,
        )
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_preflight_requires_corrected_source(monkeypatch):
    from app.services import (
        payroll_severance_corrected_source_reconciliation
        as source_module,
        payroll_severance_execution_readiness as readiness_module,
    )
    from app.services.payroll_severance_trusted_classification import (
        require_trusted_severance_snapshot,
    )

    proposal, case, review, link, journal = evidence()

    db = Obj(
        scalar=AsyncMock(
            side_effect=[
                proposal,
                case,
                review,
                link,
                journal,
            ]
        )
    )

    trusted = AsyncMock(return_value=Obj())
    source_check = AsyncMock(
        return_value=Obj(
            company_id=1,
            financial_proposal_id=20,
            original_source_id=case.original_severance_source_id
                if hasattr(case, "original_severance_source_id")
                else 100,
            source_chain_verified=True,
            proposed_gross_reconciled=True,
            posting_authorized=False,
            corrected_statutory_verified=False,
        )
    )

    case.original_severance_source_id = 100

    monkeypatch.setattr(
        readiness_module,
        "evaluate_severance_execution_readiness",
        readiness_module.evaluate_severance_execution_readiness,
    )
    monkeypatch.setattr(
        source_module,
        "load_corrected_severance_source_reconciliation",
        source_check,
    )

    import app.services.payroll_severance_trusted_classification as trusted_module
    monkeypatch.setattr(
        trusted_module,
        "require_trusted_severance_snapshot",
        trusted,
    )

    result = await readiness_module.assess_severance_execution_readiness(
        db,
        company_id=1,
        financial_proposal_id=20,
    )

    assert result.corrected_source_chain_verified
    assert result.proposed_gross_reconciled
    assert result.original_source_verified
    assert not result.corrected_statutory_source_verified
    assert not result.posting_authorized
    assert "corrected_trusted_statutory_source_required" in (
        result.blockers
    )
    source_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_preflight_rejects_invalid_revision_lineage(
    monkeypatch,
):
    from app.services import (
        payroll_severance_corrected_source_reconciliation
        as source_module,
        payroll_severance_execution_readiness as readiness_module,
    )
    from app.services.payroll_severance_corrected_source_reconciliation import (
        CorrectedSeveranceSourceError,
    )

    proposal, case, review, link, journal = evidence()

    db = Obj(
        scalar=AsyncMock(
            side_effect=[
                proposal,
                case,
                review,
                link,
                journal,
            ]
        )
    )

    import app.services.payroll_severance_trusted_classification as trusted_module
    monkeypatch.setattr(
        trusted_module,
        "require_trusted_severance_snapshot",
        AsyncMock(return_value=Obj()),
    )

    source_check = AsyncMock(
        side_effect=CorrectedSeveranceSourceError(
            "Broken source revision chain"
        )
    )

    monkeypatch.setattr(
        source_module,
        "load_corrected_severance_source_reconciliation",
        source_check,
    )

    result = await readiness_module.assess_severance_execution_readiness(
        db,
        company_id=1,
        financial_proposal_id=20,
    )

    assert not result.corrected_source_chain_verified
    assert not result.proposed_gross_reconciled
    assert not result.posting_authorized
    assert "corrected_source_lineage_not_verified" in (
        result.blockers
    )


@pytest.mark.asyncio
async def test_database_preflight_rejects_misbound_source_result(
    monkeypatch,
):
    from app.services import (
        payroll_severance_corrected_source_reconciliation
        as source_module,
        payroll_severance_execution_readiness as readiness_module,
    )

    proposal, case, review, link, journal = evidence()
    case.original_severance_source_id = 100

    db = Obj(
        scalar=AsyncMock(
            side_effect=[
                proposal,
                case,
                review,
                link,
                journal,
            ]
        )
    )

    import app.services.payroll_severance_trusted_classification as trusted_module
    monkeypatch.setattr(
        trusted_module,
        "require_trusted_severance_snapshot",
        AsyncMock(return_value=Obj()),
    )

    monkeypatch.setattr(
        source_module,
        "load_corrected_severance_source_reconciliation",
        AsyncMock(
            return_value=Obj(
                company_id=2,
                financial_proposal_id=20,
                original_source_id=100,
                source_chain_verified=True,
                proposed_gross_reconciled=True,
                posting_authorized=False,
                corrected_statutory_verified=False,
            )
        ),
    )

    result = await readiness_module.assess_severance_execution_readiness(
        db,
        company_id=1,
        financial_proposal_id=20,
    )

    assert not result.corrected_source_chain_verified
    assert "corrected_source_lineage_not_verified" in result.blockers
    assert not result.posting_authorized
