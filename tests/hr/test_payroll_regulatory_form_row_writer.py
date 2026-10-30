from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.services.payroll_regulatory_form_row_writer import (
    prepare_form_rows,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)
from app.services.payroll_regulatory_mapping_registry import (
    ApprovedRegulatoryMapping,
)
from test_payroll_regulatory_source_reconciliation import example


def setup(kind):
    report = SimpleNamespace(
        company_id=1,
        payroll_period_id=10,
        report_kind=kind,
    )

    readiness = example()
    readiness["report_kind"] = kind
    readiness["blocking_issues"] = []

    mapping = ApprovedRegulatoryMapping(
        mapping_id=17,
        report_kind=kind,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        official_reference="TEST-ONLY-NOT-LEGAL-APPROVAL",
        source_sha256="a" * 64,
        approved=True,
    )

    evidence = {
        "payroll_calculation_id": 4,
        "normative_mapping_id": 17,
        "normative_source_sha256": "a" * 64,
        "official_reference":
            "TEST-ONLY-NOT-LEGAL-APPROVAL",
        "review_reference": "TEST-REVIEW",
        "category_code": "TEST-CATEGORY",
        "income_month": date(2026, 10, 1),
        "accrued_amount": D("1000.00"),
    }

    return report, readiness, {17: mapping}, evidence


def test_d1_real_payroll_amounts_become_draft_row():
    report, readiness, approvals, evidence = setup("d1")
    evidence["usc_amount"] = D("220.00")
    evidence["insured_days"] = 31

    rows = prepare_form_rows(
        report=report,
        readiness=readiness,
        evidence=[evidence],
        approvals=approvals,
    )

    assert len(rows) == 1
    assert rows[0]["row_code"] == "D1_REVIEWED_SOURCE_V1"
    assert rows[0]["payroll_calculation_id"] == 4
    assert '"usc_amount":"220.00"' in rows[0]["payload_json"]


def test_4df_uses_verified_payment_fields():
    report, readiness, approvals, evidence = setup("4df")

    evidence.update({
        "pit_accrued": D("180.00"),
        "levy_accrued": D("50.00"),
        "payment_date": date(2026, 10, 30),
        "paid_amount": D("770.00"),
        "pit_remitted": D("180.00"),
        "levy_remitted": D("50.00"),
        "payment_evidence_reference": "TEST-PAYMENT",
        "tax_remittance_evidence_reference": "TEST-TAX",
    })

    rows = prepare_form_rows(
        report=report,
        readiness=readiness,
        evidence=[evidence],
        approvals=approvals,
    )

    assert len(rows) == 1
    assert rows[0]["row_code"] == "4DF_REVIEWED_SOURCE_V1"
    assert '"pit_accrued":"180.00"' in rows[0]["payload_json"]


def test_unapproved_mapping_rejected():
    report, readiness, _, evidence = setup("d1")
    evidence["usc_amount"] = D("220.00")
    evidence["insured_days"] = 31

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="Independent normative mapping",
    ):
        prepare_form_rows(
            report=report,
            readiness=readiness,
            evidence=[evidence],
            approvals={},
        )


def test_4df_wrong_tax_rejected():
    report, readiness, approvals, evidence = setup("4df")

    evidence.update({
        "pit_accrued": D("100.00"),
        "levy_accrued": D("50.00"),
        "payment_date": date(2026, 10, 30),
        "paid_amount": D("770.00"),
        "pit_remitted": D("100.00"),
        "levy_remitted": D("50.00"),
        "payment_evidence_reference": "TEST-PAYMENT",
        "tax_remittance_evidence_reference": "TEST-TAX",
    })

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="do not reconcile",
    ):
        prepare_form_rows(
            report=report,
            readiness=readiness,
            evidence=[evidence],
            approvals=approvals,
        )


def test_d5_and_d6_not_fabricated():
    for kind in ("d5", "d6"):
        report, readiness, approvals, evidence = setup(kind)

        with pytest.raises(
            PayrollRegulatoryReportError,
            match="Only D1 and 4DF",
        ):
            prepare_form_rows(
                report=report,
                readiness=readiness,
                evidence=[evidence],
                approvals=approvals,
            )
