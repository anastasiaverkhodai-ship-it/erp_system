"""4DF posted payment and D6 basis-evidence review.

Independent payroll records and existing posted-payment
verification are authoritative for financial provenance.

No unapproved gross-paid attribution, income category,
D6 basis mapping, or statutory generation is permitted.
"""

import hashlib
import json
import re
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_persisted_source_verifier import (
    verify_persisted_payroll_sources,
)
from app.services.payroll_regulatory_payment_evidence import (
    verify_4df_posted_payment_evidence,
)


SCHEMA = "ERP_4DF_D6_EVIDENCE_GATE_V1"


class EvidenceGateError(ValueError):
    pass


def positive(value, field):
    if type(value) is not int or value <= 0:
        raise EvidenceGateError("Invalid " + field)
    return value


def money(value, field):
    if (
        isinstance(value, (bool, float))
        or not isinstance(value, (str, int, Decimal))
    ):
        raise EvidenceGateError("Invalid amount type: " + field)

    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise EvidenceGateError("Invalid amount: " + field) from None

    if (
        not result.is_finite()
        or result < 0
        or result != result.quantize(Decimal("0.01"))
    ):
        raise EvidenceGateError(
            "Invalid amount precision: " + field
        )

    return f"{result:.2f}"


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def fingerprint(value):
    return hashlib.sha256(
        canonical(value).encode("utf-8")
    ).hexdigest()


def date_text(value, label):
    if type(value) is not date:
        raise EvidenceGateError(
            "Verified date object required: " + label
        )
    return value.isoformat()


def payment_manifest(
    *,
    company_id,
    payroll_period_id,
    allocations,
    calculation_sources,
):
    """Unique posted-evidence allocation contract.

    An input manifest alone does NOT verify payment postings.
    The PostgreSQL verifier must subsequently run.
    """

    positive(company_id, "company")
    positive(payroll_period_id, "period")

    if not isinstance(calculation_sources, list):
        raise EvidenceGateError("Payroll sources required")

    sources = {}

    for source in calculation_sources:
        if not isinstance(source, dict):
            raise EvidenceGateError("Invalid payroll source")

        calc = positive(
            source.get("payroll_calculation_id"),
            "calculation",
        )
        employee = positive(
            source.get("employee_id"),
            "employee",
        )

        if calc in sources:
            raise EvidenceGateError("Duplicate payroll calculation")

        sources[calc] = employee

    if not sources:
        raise EvidenceGateError("Empty payroll source set")

    if not isinstance(allocations, list) or not allocations:
        raise EvidenceGateError("Payment allocations required")

    rows = []
    seen_payments = set()
    seen_taxes = set()

    for allocation in allocations:
        if not isinstance(allocation, dict):
            raise EvidenceGateError("Invalid allocation record")

        calc = positive(
            allocation.get("payroll_calculation_id"),
            "allocation calculation",
        )

        if calc not in sources:
            raise EvidenceGateError(
                "Unknown or cross-period calculation"
            )

        employee = sources[calc]

        if (
            "employee_id" in allocation
            and allocation["employee_id"] != employee
        ):
            raise EvidenceGateError("Employee identity mismatch")

        when = date_text(
            allocation.get("payment_date"),
            "payment date",
        )

        payments = allocation.get("payroll_disbursement_ids")
        remittances = allocation.get("tax_remittance_line_ids")

        for refs, used, label in (
            (payments, seen_payments, "payment"),
            (remittances, seen_taxes, "tax remittance"),
        ):
            if (
                not isinstance(refs, list)
                or not refs
                or any(type(v) is not int or v <= 0 for v in refs)
                or len(set(refs)) != len(refs)
            ):
                raise EvidenceGateError(
                    "Invalid " + label + " references"
                )

            if used.intersection(refs):
                raise EvidenceGateError(
                    "Reused " + label + " reference"
                )

            used.update(refs)

        expected_payment = (
            "payroll_disbursements:"
            + ",".join(str(x) for x in sorted(payments))
        )
        expected_tax = (
            "payroll_tax_remittance_lines:"
            + ",".join(str(x) for x in sorted(remittances))
        )

        if (
            allocation.get("payment_evidence_reference")
            != expected_payment
        ):
            raise EvidenceGateError(
                "Unbound payment reference"
            )

        if (
            allocation.get("tax_remittance_evidence_reference")
            != expected_tax
        ):
            raise EvidenceGateError(
                "Unbound tax remittance reference"
            )

        rows.append({
            "calculation_id": calc,
            "employee_id": employee,
            "payment_date": when,
            "payroll_disbursement_ids": sorted(payments),
            "tax_remittance_line_ids": sorted(remittances),
            "net_paid": money(
                allocation.get("paid_amount"), "paid amount"
            ),
            "pit_remitted": money(
                allocation.get("pit_remitted"), "PIT remitted"
            ),
            "levy_remitted": money(
                allocation.get("levy_remitted"), "levy remitted"
            ),
        })

    rows.sort(key=canonical)

    body = {
        "schema": SCHEMA,
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "source_count": len(sources),
        "allocation_count": len(rows),
        "rows": rows,
        "financial_source_status": "PENDING_POSTGRESQL_VERIFICATION",
        "payment_specific_withholding_verified": False,
        "gross_paid_income_approved": False,
        "official_generation": "BLOCKED",
    }

    return {
        "body": body,
        "sha256": fingerprint(body),
    }


def review_d6_basis_candidates(
    *,
    company_id,
    payroll_period_id,
    candidates,
):
    """Validate candidate syntax and evidence, not normative meaning.

    Official eight-digit code mapping must be independently
    checked against its applicable statutory reference.
    """

    positive(company_id, "company")
    positive(payroll_period_id, "period")

    if not isinstance(candidates, list):
        raise EvidenceGateError("D6 candidates must be a list")

    seen = set()
    result = []

    for item in candidates:
        if not isinstance(item, dict):
            raise EvidenceGateError("Invalid D6 candidate")

        employee = positive(item.get("employee_id"), "employee")
        contract = positive(item.get("contract_id"), "contract")

        code = item.get("basis_code")
        if type(code) is not str or not re.fullmatch(
            r"[0-9]{8}", code
        ):
            raise EvidenceGateError(
                "D6 basis code must have eight digits"
            )

        start = item.get("period_start")
        end = item.get("period_end")

        if type(start) is not date or type(end) is not date:
            raise EvidenceGateError(
                "D6 period dates required"
            )

        if start > end:
            raise EvidenceGateError(
                "D6 period ends before it starts"
            )

        reference = item.get("basis_evidence_reference")
        if (
            type(reference) is not str
            or not reference.strip()
            or len(reference) > 240
        ):
            raise EvidenceGateError(
                "D6 documentary evidence reference required"
            )

        key = (
            employee, contract, code,
            start.isoformat(), end.isoformat()
        )

        if key in seen:
            raise EvidenceGateError(
                "Duplicate D6 candidate"
            )

        seen.add(key)

        result.append({
            "employee_id": employee,
            "contract_id": contract,
            "basis_code": code,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "basis_evidence_reference": reference.strip(),
            "source_binding_verified": False,
            "normative_mapping_approved": False,
        })

    result.sort(key=canonical)

    body = {
        "schema": "ERP_D6_BASIS_CANDIDATES_V1",
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "candidate_count": len(result),
        "candidates": result,
        "statutory_basis_approved": False,
        "official_generation": "BLOCKED",
    }

    return {
        "body": body,
        "sha256": fingerprint(body),
    }


async def verify_posted_payment_manifest(
    db,
    *,
    company_id,
    payroll_period_id,
    reviewed_allocations,
):
    """Reload live payroll and check real posted journals."""

    readiness = await payroll_regulatory_readiness(
        db,
        company_id=positive(company_id, "company"),
        payroll_period_id=positive(
            payroll_period_id, "period"
        ),
        report_kind="4df",
    )

    verified = await verify_persisted_payroll_sources(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        readiness=readiness,
    )

    if verified.get("verified") is not True:
        raise EvidenceGateError(
            "Persisted payroll source verification failed"
        )

    manifest = payment_manifest(
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        allocations=reviewed_allocations,
        calculation_sources=readiness["calculation_sources"],
    )

    employees = {
        row["payroll_calculation_id"]: row["employee_id"]
        for row in readiness["calculation_sources"]
    }

    checked = deepcopy(reviewed_allocations)

    for item in checked:
        item["_verified_employee_id"] = (
            employees[item["payroll_calculation_id"]]
        )

    # Real existing PG verifier is authoritative for
    # posted payments and tax-remittance records.
    await verify_4df_posted_payment_evidence(
        db,
        company_id=company_id,
        reviewed_allocations=checked,
    )

    body = deepcopy(manifest["body"])
    body["financial_source_status"] = "POSTED_EVIDENCE_VERIFIED"

    # Posted taxes do not prove payment-specific withholding.
    body["payment_specific_withholding_verified"] = False
    body["gross_paid_income_approved"] = False
    body["official_generation"] = "BLOCKED"

    return {
        "body": body,
        "sha256": fingerprint(body),
    }


def verify_manifest(manifest):
    if (
        type(manifest) is not dict
        or set(manifest) != {"body", "sha256"}
        or type(manifest["body"]) is not dict
    ):
        raise EvidenceGateError("Invalid manifest")

    body = manifest["body"]

    if (
        body.get("official_generation") != "BLOCKED"
        or body.get("gross_paid_income_approved") is not False
        or body.get("payment_specific_withholding_verified")
            is not False
        or body.get("schema") != SCHEMA
        or body.get("financial_source_status") not in (
            "PENDING_POSTGRESQL_VERIFICATION",
            "POSTED_EVIDENCE_VERIFIED",
        )
    ):
        raise EvidenceGateError(
            "Unapproved statutory financial claim"
        )

    if manifest["sha256"] != fingerprint(body):
        raise EvidenceGateError(
            "Payment manifest digest mismatch"
        )

    return True
