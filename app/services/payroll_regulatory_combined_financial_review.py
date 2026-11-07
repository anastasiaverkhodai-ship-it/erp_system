"""Combined D1/4DF/D6 source-bound financial review.

Review only. Never creates or authorizes statutory rows.
"""

import hashlib
import json
from decimal import Decimal

from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_source_reconciliation import (
    reconcile_regulatory_sources,
)
from app.services.payroll_regulatory_d1_completeness import (
    build_d1_completeness_control,
)
from app.services.payroll_regulatory_4df_integrity import (
    verify_4df_financial_projection,
)
from app.services.payroll_regulatory_financial_reconciliation import (
    inspect_persisted_financial_sources,
)
from app.services.payroll_regulatory_persisted_source_verifier import (
    verify_persisted_payroll_sources,
)
from app.services.payroll_d6_evidence_service import (
    period_evidence,
)


class CombinedFinancialReviewError(ValueError):
    pass


SCHEMA = "D1_4DF_D6_FINANCIAL_REVIEW_V1"


def positive_id(value, field):
    if type(value) is not int or value <= 0:
        raise CombinedFinancialReviewError(
            "Invalid " + field
        )


def canonical_digest(value):
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def amount(value, field):
    if (
        isinstance(value, (bool, float))
        or not isinstance(value, (int, str, Decimal))
    ):
        raise CombinedFinancialReviewError(
            "Invalid monetary type: " + field
        )

    try:
        result = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise CombinedFinancialReviewError(
            "Invalid monetary amount: " + field
        ) from exc

    if (
        not result.is_finite()
        or result < 0
        or result.as_tuple().exponent < -2
    ):
        raise CombinedFinancialReviewError(
            "Invalid monetary precision/value: " + field
        )

    return result


def review_financial_groups(
    *,
    company_id,
    payroll_period_id,
    calculation_sources,
    totals,
    groups,
):
    """Validate 4DF totals without claiming approved paid-gross."""

    if groups is None:
        return {
            "status": "NOT_VERIFIED",
            "reason": "DATED_4DF_FINANCIAL_GROUPS_NOT_SUPPLIED",
            "statutory_mapping_approved": False,
            "gross_paid_income_verified": False,
        }

    if not isinstance(groups, (list, tuple)) or not groups:
        raise CombinedFinancialReviewError(
            "Nonempty 4DF financial groups required"
        )

    expected_ids = []
    employees = {}

    for source in calculation_sources:
        source_id = source["payroll_calculation_id"]
        employee_id = source["employee_id"]

        positive_id(source_id, "calculation ID")
        positive_id(employee_id, "employee ID")

        if source_id in employees:
            raise CombinedFinancialReviewError(
                "Duplicate payroll calculation"
            )

        employees[source_id] = employee_id
        expected_ids.append(source_id)

    if not expected_ids:
        raise CombinedFinancialReviewError(
            "No independent payroll calculations"
        )

    manifest = verify_4df_financial_projection(
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        financial_groups=groups,
        expected_calculation_ids=expected_ids,
    )

    sums = {
        "gross_accrued": Decimal("0.00"),
        "pit_accrued": Decimal("0.00"),
        "levy_accrued": Decimal("0.00"),
    }

    for group in groups:
        for source_id in group["payroll_calculation_ids"]:
            if employees[source_id] != group["employee_id"]:
                raise CombinedFinancialReviewError(
                    "4DF employee/payroll identity mismatch"
                )

        for field in sums:
            sums[field] += amount(group[field], field)

        if (
            group.get("gross_paid_income_verified") is True
            or group.get("statutory_approved") is not False
        ):
            raise CombinedFinancialReviewError(
                "Unapproved 4DF statutory attribution"
            )

    expected = {
        "gross_accrued": amount(totals.gross, "payroll gross"),
        "pit_accrued": amount(totals.pit, "payroll PIT"),
        "levy_accrued": amount(
            totals.military_levy,
            "payroll military levy",
        ),
    }

    if sums != expected:
        raise CombinedFinancialReviewError(
            "4DF accrued totals differ from payroll sources"
        )

    return {
        "status": "AMOUNT_RECONCILED_SOURCE_BINDING_PENDING",
        "source_count": manifest["source_count"],
        "group_count": manifest["group_count"],
        "manifest_sha256": manifest["manifest_sha256"],
        "gross_accrued": str(sums["gross_accrued"]),
        "pit_accrued": str(sums["pit_accrued"]),
        "levy_accrued": str(sums["levy_accrued"]),
        "gross_paid_income_verified": False,
        "statutory_mapping_approved": False,
        "official_generation": "BLOCKED",
    }


def validate_combined_review(review):
    if type(review) is not dict:
        raise CombinedFinancialReviewError(
            "Combined review required"
        )

    expected = {
        "schema", "company_id", "payroll_period_id",
        "payroll", "d1", "four_df", "d6",
        "financial_schema_inspection",
        "review_only", "official_generation",
        "official_rows_generated",
        "statutory_mapping_approved",
        "review_sha256",
    }

    if set(review) != expected:
        raise CombinedFinancialReviewError(
            "Combined review schema mismatch"
        )

    if (
        review["schema"] != SCHEMA
        or review["review_only"] is not True
        or review["official_generation"] != "BLOCKED"
        or review["statutory_mapping_approved"] is not False
        or type(review["official_rows_generated"]) is not int
        or review["official_rows_generated"] != 0
    ):
        raise CombinedFinancialReviewError(
            "Combined review statutory gate mismatch"
        )

    positive_id(review["company_id"], "company_id")
    positive_id(review["payroll_period_id"], "payroll_period_id")

    body = {
        key: value for key, value in review.items()
        if key != "review_sha256"
    }

    if canonical_digest(body) != review["review_sha256"]:
        raise CombinedFinancialReviewError(
            "Combined review digest mismatch"
        )

    for section in ("payroll", "d1", "four_df", "d6"):
        if not isinstance(review[section], dict):
            raise CombinedFinancialReviewError(
                "Invalid review section: " + section
            )

    if review["d1"].get("official_statutory_generation") != "BLOCKED":
        raise CombinedFinancialReviewError(
            "D1 statutory gate mismatch"
        )

    if review["four_df"].get(
        "statutory_mapping_approved"
    ) is not False:
        raise CombinedFinancialReviewError(
            "4DF statutory gate mismatch"
        )

    if review["d6"].get("official_statutory_generation") != "BLOCKED":
        raise CombinedFinancialReviewError(
            "D6 statutory gate mismatch"
        )

    return True


async def build_combined_financial_review(
    db,
    *,
    company_id,
    payroll_period_id,
    financial_groups=None,
):
    positive_id(company_id, "company_id")
    positive_id(payroll_period_id, "payroll_period_id")

    try:
        readiness = await payroll_regulatory_readiness(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            report_kind="d1",
        )

        await verify_persisted_payroll_sources(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            readiness=readiness,
        )

        sources = readiness["calculation_sources"]
        if not sources:
            raise CombinedFinancialReviewError(
                "Empty payroll calculation sources"
            )

        totals = reconcile_regulatory_sources(readiness)

        d1 = await build_d1_completeness_control(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
        )

        d1_result = d1["d1"]

        if (
            d1["official_statutory_generation"] != "BLOCKED"
            or d1_result["source_integrity"] != "PASS"
            or amount(
                d1_result["employer_contribution_total"],
                "D1 USC",
            ) != amount(
                totals.usc,
                "payroll USC",
            )
        ):
            raise CombinedFinancialReviewError(
                "D1/USC independent reconciliation mismatch"
            )

        four_df = review_financial_groups(
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            calculation_sources=sources,
            totals=totals,
            groups=financial_groups,
        )

        d6 = await period_evidence(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
        )

        if (
            d6["official_statutory_generation"] != "BLOCKED"
            or d6["normative_mapping"] != "NOT_APPROVED"
        ):
            raise CombinedFinancialReviewError(
                "D6 statutory mapping unexpectedly enabled"
            )

        inspection = await inspect_persisted_financial_sources(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
        )

        if inspection["official_generation"] != "BLOCKED":
            raise CombinedFinancialReviewError(
                "Financial schema review unexpectedly enabled"
            )

    except Exception as exc:
        raise CombinedFinancialReviewError(
            "Persisted D1/4DF/D6 financial review rejected"
        ) from exc

    review = {
        "schema": SCHEMA,
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "payroll": {
            "source_count": totals.source_count,
            "calculation_ids": list(totals.source_ids),
            "gross": str(totals.gross),
            "net": str(totals.net),
            "pit": str(totals.pit),
            "military_levy": str(totals.military_levy),
            "usc": str(totals.usc),
        },
        "d1": d1_result,
        "four_df": four_df,
        "d6": {
            "coverage": d6["coverage"],
            "applicability": d6["applicability"],
            "missing_contract_ids": d6["missing_contract_ids"],
            "source_sha256": canonical_digest(d6["records"]),
            "evidence_count": len(d6["records"]),
            "normative_mapping": d6["normative_mapping"],
            "official_statutory_generation": "BLOCKED",
        },
        "financial_schema_inspection": {
            "status": inspection["status"],
            "reason": inspection["reason"],
        },
        "review_only": True,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
        "statutory_mapping_approved": False,
    }

    review["review_sha256"] = canonical_digest(review)
    validate_combined_review(review)
    return review


async def verify_combined_financial_review(
    db,
    review,
    *,
    financial_groups=None,
):
    validate_combined_review(review)

    rebuilt = await build_combined_financial_review(
        db,
        company_id=review["company_id"],
        payroll_period_id=review["payroll_period_id"],
        financial_groups=financial_groups,
    )

    if rebuilt != review:
        raise CombinedFinancialReviewError(
            "Combined financial review is stale"
        )

    return True
