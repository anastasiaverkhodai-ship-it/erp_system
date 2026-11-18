"""Multi-revision corrections must not subtract accumulated history as a baseline.

This is a domain regression, not a claim of persisted PostgreSQL provenance.
"""
from copy import deepcopy

from test_payroll_regulatory_correction_delta import evidence
from app.services.payroll_regulatory_correction_delta import reconcile_correction_delta
from app.services.payroll_regulatory_financial_reconciliation import FinancialReconciliationError
from app.services.payroll_regulatory_revision_chain_review import review_revision_chain


def three_revision_evidence():
    _, lines, sources = evidence()
    # One employee: v1=1000, v2=1200, v3=1300. The two valid deltas are
    # +300 from v1 or +100 from v2; 1300-(1000+1200) is neither.
    calculations = [dict(id=identifier, company_id=1, payroll_period_id=2,
        employment_contract_id=10, revision=revision)
        for revision, identifier in enumerate((11, 12, 13), 1)]
    corrections = [dict(company_id=1, original_payroll_calculation_id=a,
        replacement_payroll_calculation_id=b) for a, b in ((11, 12), (12, 13))]
    statutory = [dict(company_id=1, payroll_calculation_id=identifier)
        for identifier in (11, 12, 13)]
    chain = review_revision_chain(calculations, corrections, statutory,
        company_id=1, payroll_period_id=2)
    assert chain["status"] == "REVISION_CHAIN_VERIFIED"
    assert chain["active_calculation_ids"] == [13]
    assert chain["historical_calculation_ids"] == [11, 12]
    # Match the input contract of the persisted adapter. This test remains
    # domain-only and does not certify that the fixture came from a database.
    chain["data_origin"] = "PERSISTED_POSTGRESQL"
    third_line = deepcopy(lines[-1])
    third_line.update(id=103, payroll_calculation_id=13,
        base_amount="1300.00", amount="234.00")
    lines.append(third_line)
    third_source = deepcopy(sources[-1])
    third_source.update(id=203, statutory_line_id=103,
        payroll_calculation_line_id=303, payroll_calculation_id=13,
        source_amount="1300.00")
    sources.append(third_source)
    return chain, lines, sources


def test_three_revisions_without_selected_baseline_fail_closed():
    chain, lines, sources = three_revision_evidence()
    chain.pop("immediate_predecessors")
    import pytest
    with pytest.raises(FinancialReconciliationError, match="baseline"):
        reconcile_correction_delta(chain, lines, sources,
            company_id=1, payroll_period_id=2)


def test_three_revisions_use_immediate_predecessor():
    chain, lines, sources = three_revision_evidence()
    result = reconcile_correction_delta(chain, lines, sources,
        company_id=1, payroll_period_id=2)
    assert result["status"] == "CORRECTION_COMPONENT_DELTA_RECONCILED"
    assert result["baseline_calculation_ids"] == [12]
    assert result["components"][0]["base_delta"] == "100.00"
    assert result["components"][0]["tax_delta"] == "18.00"


def test_invalid_older_sources_still_block():
    chain, lines, sources = three_revision_evidence()
    sources[0]["source_amount"] = "999.00"
    result = reconcile_correction_delta(chain, lines, sources,
        company_id=1, payroll_period_id=2)
    assert result["status"] == "NOT_VERIFIED"
