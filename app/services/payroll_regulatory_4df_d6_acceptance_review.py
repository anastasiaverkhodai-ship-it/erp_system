"""Independent 4DF/D6 financial readiness control.

This service provides internal audit evidence only.
It never computes an unapproved statutory gross-paid
amount, approves normative mappings, or generates forms.
"""

from copy import deepcopy
from datetime import date

from app.services.payroll_regulatory_combined_financial_review import (
    build_combined_financial_review,
    validate_combined_review,
    canonical_digest,
)
from app.services.payroll_regulatory_persisted_source_verifier import (
    verify_persisted_payroll_sources,
)
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_payment_evidence import (
    verify_4df_posted_payment_evidence,
)


SCHEMA = "4DF_D6_ACCEPTANCE_REVIEW_V1"


class FourDFD6AcceptanceError(ValueError):
    pass


def positive(value, name):
    if type(value) is not int or value <= 0:
        raise FourDFD6AcceptanceError("Invalid " + name)
    return value


def verify_review(review):
    if not isinstance(review, dict):
        raise FourDFD6AcceptanceError("Review required")

    expected = {
        "schema",
        "company_id",
        "payroll_period_id",
        "combined_review_sha256",
        "d1_source_integrity",
        "four_df_accrual_status",
        "four_df_gross_paid_status",
        "four_df_posted_payment_status",
        "d6_source_status",
        "d6_normative_status",
        "d6_evidence_sha256",
        "d6_evidence_count",
        "review_only",
        "official_generation",
        "official_rows_generated",
        "review_sha256",
    }

    if set(review) != expected:
        raise FourDFD6AcceptanceError(
            "Unexpected review schema"
        )

    positive(review["company_id"], "company_id")
    positive(review["payroll_period_id"], "payroll_period_id")

    if (
        review["schema"] != SCHEMA
        or review["review_only"] is not True
        or review["four_df_gross_paid_status"]
            != "NOT_NORMATIVELY_VERIFIED"
        or review["d6_normative_status"] != "NOT_APPROVED"
        or review["official_generation"] != "BLOCKED"
        or review["official_rows_generated"] != 0
        or type(review["d6_evidence_count"]) is not int
        or review["d6_evidence_count"] < 0
        or review["four_df_posted_payment_status"]
            not in ("NOT_SUPPLIED", "POSTED_EVIDENCE_VERIFIED")
        or review["four_df_accrual_status"]
            not in (
                "NOT_VERIFIED",
                "AMOUNT_RECONCILED_SOURCE_BINDING_PENDING",
            )
        or review["d1_source_integrity"] != "PASS"
    ):
        raise FourDFD6AcceptanceError(
            "Review-only statutory guard failed"
        )

    if review["d6_source_status"] not in (
        "SOURCE_REVIEW_ONLY",
        "SOURCE_GAPS_PRESENT",
    ):
        raise FourDFD6AcceptanceError(
            "Invalid D6 evidence status"
        )

    for key in (
        "combined_review_sha256",
        "d6_evidence_sha256",
        "review_sha256",
    ):
        value = review[key]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise FourDFD6AcceptanceError(
                "Invalid hash: " + key
            )

    body = {
        key: value
        for key, value in review.items()
        if key != "review_sha256"
    }

    if review["review_sha256"] != canonical_digest(body):
        raise FourDFD6AcceptanceError(
            "Review fingerprint mismatch"
        )

    return True


async def build_4df_d6_acceptance_review(
    db,
    *,
    company_id,
    payroll_period_id,
    financial_groups=None,
    reviewed_allocations=None,
):
    positive(company_id, "company_id")
    positive(payroll_period_id, "payroll_period_id")

    combined = await build_combined_financial_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        financial_groups=financial_groups,
    )
    validate_combined_review(combined)

    if combined["official_generation"] != "BLOCKED":
        raise FourDFD6AcceptanceError(
            "Combined financial gate unexpectedly enabled"
        )

    d1 = combined["d1"]
    d6 = combined["d6"]
    four_df = combined["four_df"]

    if (
        d1.get("source_integrity") != "PASS"
        or d6.get("normative_mapping") != "NOT_APPROVED"
        or d6.get("official_statutory_generation") != "BLOCKED"
        or four_df.get("statutory_mapping_approved") is not False
    ):
        raise FourDFD6AcceptanceError(
            "Unapproved financial or D6 state"
        )

    payment_status = "NOT_SUPPLIED"

    if reviewed_allocations is not None:
        if (
            not isinstance(reviewed_allocations, list)
            or not reviewed_allocations
        ):
            raise FourDFD6AcceptanceError(
                "Payment allocations must be nonempty"
            )

        readiness = await payroll_regulatory_readiness(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            report_kind="4df",
        )

        independent = await verify_persisted_payroll_sources(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            readiness=readiness,
        )

        if independent.get("verified") is not True:
            raise FourDFD6AcceptanceError(
                "Current payroll source not independently verified"
            )

        sources = {
            row["payroll_calculation_id"]: row
            for row in readiness["calculation_sources"]
        }

        if not sources:
            raise FourDFD6AcceptanceError(
                "Empty 4DF payment source set"
            )

        allocations = deepcopy(reviewed_allocations)

        for item in allocations:
            if not isinstance(item, dict):
                raise FourDFD6AcceptanceError(
                    "Invalid payment evidence"
                )

            source_id = positive(
                item.get("payroll_calculation_id"),
                "payment calculation",
            )

            if source_id not in sources:
                raise FourDFD6AcceptanceError(
                    "Cross-company or stale payment source"
                )

            employee_id = sources[source_id]["employee_id"]

            if (
                "employee_id" in item
                and item["employee_id"] != employee_id
            ):
                raise FourDFD6AcceptanceError(
                    "Payment employee mismatch"
                )

            if type(item.get("payment_date")) is not date:
                raise FourDFD6AcceptanceError(
                    "Real posted payment date required"
                )

            item["_verified_employee_id"] = employee_id

        # This verifier checks real PostgreSQL journals,
        # payment IDs, tax remittance lines, amounts and dates.
        await verify_4df_posted_payment_evidence(
            db,
            company_id=company_id,
            reviewed_allocations=allocations,
        )

        payment_status = "POSTED_EVIDENCE_VERIFIED"

    missing = d6.get("missing_contract_ids")
    if not isinstance(missing, (tuple, list)):
        raise FourDFD6AcceptanceError(
            "Invalid D6 source coverage"
        )

    evidence_count = d6.get("evidence_count")
    if type(evidence_count) is not int or evidence_count < 0:
        raise FourDFD6AcceptanceError(
            "Invalid D6 evidence count"
        )

    review = {
        "schema": SCHEMA,
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "combined_review_sha256": combined["review_sha256"],
        "d1_source_integrity": "PASS",
        "four_df_accrual_status": four_df["status"],
        "four_df_gross_paid_status":
            "NOT_NORMATIVELY_VERIFIED",
        "four_df_posted_payment_status": payment_status,
        "d6_source_status": (
            "SOURCE_GAPS_PRESENT"
            if missing
            else "SOURCE_REVIEW_ONLY"
        ),
        "d6_normative_status": "NOT_APPROVED",
        "d6_evidence_sha256": d6["source_sha256"],
        "d6_evidence_count": evidence_count,
        "review_only": True,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }

    review["review_sha256"] = canonical_digest(review)
    verify_review(review)
    return review


async def verify_4df_d6_acceptance_review(
    db,
    report,
    *,
    financial_groups=None,
    reviewed_allocations=None,
):
    verify_review(report)

    rebuilt = await build_4df_d6_acceptance_review(
        db,
        company_id=report["company_id"],
        payroll_period_id=report["payroll_period_id"],
        financial_groups=financial_groups,
        reviewed_allocations=reviewed_allocations,
    )

    if rebuilt != report:
        raise FourDFD6AcceptanceError(
            "Review is stale against live PostgreSQL sources"
        )

    return True
