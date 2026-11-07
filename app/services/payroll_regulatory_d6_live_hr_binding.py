"""D6 candidate -> live HR contract and reporting period.

Positive HR binding does not prove statutory D6 eligibility.
4DF posted remittances do not prove payment-specific withholding.
Official generation remains blocked.
"""

from copy import deepcopy
from datetime import date

from sqlalchemy import select

from app.models.employee import Employee
from app.models.employment_contract import (
    EmploymentContract,
    EmploymentContractStatus,
)
from app.models.payroll import PayrollPeriod

from app.services.payroll_regulatory_4df_d6_evidence_gate import (
    EvidenceGateError,
    fingerprint,
    positive,
    review_d6_basis_candidates,
    verify_posted_payment_manifest,
)


SCHEMA = "ERP_D6_LIVE_HR_SOURCE_BINDING_V1"


def verify_binding(document):
    if type(document) is not dict:
        raise EvidenceGateError("Binding document required")

    if set(document) != {"body", "sha256"}:
        raise EvidenceGateError("Invalid binding envelope")

    body = document["body"]

    if type(body) is not dict or set(body) != {
        "schema",
        "company_id",
        "payroll_period_id",
        "candidate_count",
        "candidates",
        "hr_source_binding",
        "normative_code_mapping",
        "special_service_eligibility",
        "official_generation",
        "official_rows_generated",
    }:
        raise EvidenceGateError("Invalid binding schema")

    if (
        body["schema"] != SCHEMA
        or body["hr_source_binding"] != "LIVE_POSTGRESQL_VERIFIED"
        or body["normative_code_mapping"] != "NOT_APPROVED"
        or body["special_service_eligibility"] != "NOT_VERIFIED"
        or body["official_generation"] != "BLOCKED"
        or type(body["official_rows_generated"]) is not int
        or body["official_rows_generated"] != 0
    ):
        raise EvidenceGateError(
            "Unapproved statutory claim"
        )

    positive(body["company_id"], "company")
    positive(body["payroll_period_id"], "period")

    rows = body["candidates"]
    if (
        type(rows) is not list
        or type(body["candidate_count"]) is not int
        or len(rows) != body["candidate_count"]
    ):
        raise EvidenceGateError("Candidate count mismatch")

    for row in rows:
        if (
            type(row) is not dict
            or row.get("hr_binding") != "VERIFIED"
            or row.get("basis_code_mapping") != "NOT_APPROVED"
            or row.get("special_service_basis") != "NOT_VERIFIED"
        ):
            raise EvidenceGateError(
                "Unverified D6 candidate"
            )

    if document["sha256"] != fingerprint(body):
        raise EvidenceGateError("Binding digest mismatch")

    return True


async def bind_d6_candidates_to_live_hr(
    db,
    *,
    company_id,
    payroll_period_id,
    candidates,
):
    """Scope and date-check D6 candidates against real HR records."""

    positive(company_id, "company")
    positive(payroll_period_id, "period")

    draft = review_d6_basis_candidates(
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        candidates=candidates,
    )

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == payroll_period_id,
        )
    )

    if period is None:
        raise EvidenceGateError(
            "Reporting period not found for company"
        )

    period_start = period.start_date
    period_end = period.end_date

    if (
        type(period_start) is not date
        or type(period_end) is not date
        or period_start > period_end
    ):
        raise EvidenceGateError(
            "Invalid persisted reporting period"
        )

    verified = []

    for candidate in draft["body"]["candidates"]:
        contract = await db.scalar(
            select(EmploymentContract).where(
                EmploymentContract.company_id == company_id,
                EmploymentContract.id ==
                    candidate["contract_id"],
                EmploymentContract.employee_id ==
                    candidate["employee_id"],
            )
        )

        if contract is None:
            raise EvidenceGateError(
                "D6 contract missing or cross-company"
            )

        if contract.status == EmploymentContractStatus.CANCELLED:
            raise EvidenceGateError(
                "Cancelled contract cannot support D6 period"
            )

        employee = await db.scalar(
            select(Employee).where(
                Employee.company_id == company_id,
                Employee.id == candidate["employee_id"],
            )
        )

        if employee is None:
            raise EvidenceGateError(
                "D6 employee missing or cross-company"
            )

        start = date.fromisoformat(
            candidate["period_start"]
        )
        end = date.fromisoformat(
            candidate["period_end"]
        )

        if (
            start < period_start
            or end > period_end
            or start < contract.start_date
            or (
                contract.end_date is not None
                and end > contract.end_date
            )
        ):
            raise EvidenceGateError(
                "D6 candidate outside payroll/contract dates"
            )

        record = deepcopy(candidate)
        record["hr_binding"] = "VERIFIED"
        record["contract_start_date"] = (
            contract.start_date.isoformat()
        )
        record["contract_end_date"] = (
            contract.end_date.isoformat()
            if contract.end_date is not None else None
        )
        record["basis_code_mapping"] = "NOT_APPROVED"
        record["special_service_basis"] = "NOT_VERIFIED"

        verified.append(record)

    body = {
        "schema": SCHEMA,
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "candidate_count": len(verified),
        "candidates": verified,
        "hr_source_binding": "LIVE_POSTGRESQL_VERIFIED",
        "normative_code_mapping": "NOT_APPROVED",
        "special_service_eligibility": "NOT_VERIFIED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }

    result = {
        "body": body,
        "sha256": fingerprint(body),
    }
    verify_binding(result)
    return result


async def audit_4df_withholding_source_gap(
    db,
    *,
    company_id,
    payroll_period_id,
    reviewed_allocations,
):
    """Verify posted evidence without promoting remittance to withholding.

    Existing remittance lines establish payment to the budget,
    not necessarily employee withholding on a particular payout.
    """

    manifest = await verify_posted_payment_manifest(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        reviewed_allocations=reviewed_allocations,
    )

    if (
        manifest["body"]["financial_source_status"]
            != "POSTED_EVIDENCE_VERIFIED"
        or manifest["body"]["official_generation"] != "BLOCKED"
        or manifest["body"]["gross_paid_income_approved"] is not False
    ):
        raise EvidenceGateError(
            "Payment evidence approval guard failed"
        )

    body = {
        "schema": "ERP_4DF_WITHHOLDING_GAP_AUDIT_V1",
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "posted_manifest_sha256": manifest["sha256"],
        "posted_payment_evidence": "VERIFIED",
        "posted_tax_remittance_evidence": "VERIFIED",
        "payment_specific_withholding": "NOT_VERIFIED",
        "gross_paid_income": "NOT_APPROVED",
        "official_generation": "BLOCKED",
    }

    return {"body": body, "sha256": fingerprint(body)}
