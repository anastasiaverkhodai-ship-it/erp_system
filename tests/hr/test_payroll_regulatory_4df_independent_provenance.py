from copy import deepcopy

import pytest

from app.services.payroll_regulatory_4df_independent_provenance import (
    FourDFProvenanceError,
    digest,
    group_fingerprint,
    source_fingerprint,
    verify_group_fingerprints,
)


def fixture():
    source = {
        "payroll_calculation_id": 10,
        "employee_id": 20,
        "employment_contract_id": 30,
        "revision": 1,
        "gross_amount": "100.00",
        "net_amount": "77.00",
        "employee_withholding_amount": "23.00",
        "employer_contribution_amount": "22.00",
        "currency_code": "UAH",
        "components": [],
    }

    entry = {
        "source_id": 10,
        "source_sha256": source_fingerprint(
            company_id=1,
            payroll_period_id=2,
            source=source,
        ),
        "slice_number": 1,
        "slice_count": 1,
        "payment_date": "2026-09-30",
        "payment_evidence_reference": "TEST_PAYMENT",
        "tax_remittance_evidence_reference": "TEST_TAX",
    }

    group = {
        "employee_id": 20,
        "reviewed_category_code": "TEST_ONLY",
        "income_month": "2026-09-01",
        "financial_evidence": [entry],
        "payroll_calculation_ids": [10],
        "gross_accrued": "100.00",
        "pit_accrued": "18.00",
        "levy_accrued": "5.00",
        "paid_amount": "77.00",
        "pit_remitted": "18.00",
        "levy_remitted": "5.00",
    }

    group["source_fingerprint"] = group_fingerprint(
        company_id=1,
        payroll_period_id=2,
        group=group,
    )

    return source, group


def check(source, group):
    return verify_group_fingerprints(
        company_id=1,
        payroll_period_id=2,
        groups=[group],
        calculation_sources=[source],
    )


def test_independent_hash_matches_canonical_contract():
    source, group = fixture()
    result = check(source, group)
    assert result["source_count"] == 1
    assert result["slice_count"] == 1
    assert result["official_generation"] == "BLOCKED"


def test_source_hash_tampering_is_blocked():
    source, group = fixture()
    group["financial_evidence"][0]["source_sha256"] = "a" * 64
    group["source_fingerprint"] = group_fingerprint(
        company_id=1,
        payroll_period_id=2,
        group=group,
    )

    with pytest.raises(
        FourDFProvenanceError,
        match="Independent source fingerprint",
    ):
        check(source, group)


def test_recomputed_group_hash_rejects_changed_amount():
    source, group = fixture()
    group["gross_accrued"] = "99.00"
    with pytest.raises(
        FourDFProvenanceError,
        match="Financial group fingerprint",
    ):
        check(source, group)


def test_changed_persisted_source_rejected():
    source, group = fixture()
    modified = deepcopy(source)
    modified["gross_amount"] = "101.00"

    with pytest.raises(
        FourDFProvenanceError,
        match="Independent source fingerprint",
    ):
        check(modified, group)


def test_cross_employee_source_rejected():
    source, group = fixture()
    source["employee_id"] = 999
    group["financial_evidence"][0]["source_sha256"] = (
        source_fingerprint(
            company_id=1,
            payroll_period_id=2,
            source=source,
        )
    )
    group["source_fingerprint"] = group_fingerprint(
        company_id=1,
        payroll_period_id=2,
        group=group,
    )

    with pytest.raises(
        FourDFProvenanceError,
        match="Cross-employee",
    ):
        check(source, group)


def test_missing_sources_rejected():
    source, group = fixture()
    with pytest.raises(FourDFProvenanceError):
        verify_group_fingerprints(
            company_id=1,
            payroll_period_id=2,
            groups=[group],
            calculation_sources=[],
        )


def test_duplicate_groups_rejected():
    source, group = fixture()
    with pytest.raises(
        FourDFProvenanceError,
        match="reused",
    ):
        verify_group_fingerprints(
            company_id=1,
            payroll_period_id=2,
            groups=[group, deepcopy(group)],
            calculation_sources=[source],
        )


def test_canonical_digest_deterministic():
    assert digest({"x": 1, "y": 2}) == (
        digest({"y": 2, "x": 1})
    )


# PHASE13_4DF_BRIDGE_SEARCH_PATH_REGRESSION_V1
def test_bridge_uses_current_schema_for_report_metadata():
    from pathlib import Path

    source = Path(
        "app/services/"
        "payroll_regulatory_persisted_internal_bridge.py"
    ).read_text(encoding="utf-8")

    assert "FROM payroll_regulatory_reports" in source
    assert "FROM public.payroll_regulatory_reports" not in source


def test_bridge_uses_current_schema_for_report_rows():
    from pathlib import Path

    source = Path(
        "app/services/"
        "payroll_regulatory_persisted_internal_bridge.py"
    ).read_text(encoding="utf-8")

    assert "FROM payroll_regulatory_report_rows" in source
    assert (
        "FROM public.payroll_regulatory_report_rows"
        not in source
    )
