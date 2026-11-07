"""2026 regulatory evidence controls for 4DF and D6.

Source: Ukrainian Ministry of Finance Order 243/2026,
as amended by Order 284/2026.

This is a conservative internal validation contract.
It does not approve statutory mappings or generate filings.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json


class NormativeControlError(ValueError):
    pass


SCHEMA = "ERP_2026_NORMATIVE_CONTROL_V1"

SOURCE = {
    "instrument": "Ministry of Finance Order 243/2026",
    "amendment": "Order 284/2026",
    "effective_from": "2026-07-17",
    "first_legal_entity_reporting_month": "2026-07",
    "official_url":
        "https://zakon.rada.gov.ua/laws/show/z0751-26",
    "baseline_order_url":
        "https://zakon.rada.gov.ua/laws/show/z0111-15",
}


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256(value):
    return hashlib.sha256(
        canonical(value).encode("utf-8")
    ).hexdigest()


def positive(value, name):
    if type(value) is not int or value <= 0:
        raise NormativeControlError(
            "Invalid " + name
        )
    return value


def amount(value, name):
    if (
        value is None
        or isinstance(value, (bool, float))
        or not isinstance(value, (Decimal, int, str))
    ):
        raise NormativeControlError(
            "Invalid monetary type: " + name
        )

    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise NormativeControlError(
            "Invalid monetary value: " + name
        ) from None

    if (
        not result.is_finite()
        or result < 0
        or result != result.quantize(Decimal("0.01"))
    ):
        raise NormativeControlError(
            "Invalid monetary precision: " + name
        )

    return result.quantize(Decimal("0.01"))


def reporting_month(value):
    if (
        type(value) is not str
        or len(value) != 7
        or value[4] != "-"
    ):
        raise NormativeControlError(
            "Expected YYYY-MM reporting month"
        )

    try:
        parsed = date.fromisoformat(value + "-01")
    except ValueError:
        raise NormativeControlError(
            "Invalid reporting month"
        ) from None

    if parsed.strftime("%Y-%m") != value:
        raise NormativeControlError(
            "Noncanonical reporting month"
        )

    return parsed


def applicable_version(month, *, entity_type):
    """Never silently extend this contract to FOP/other agents."""

    parsed = reporting_month(month)

    if entity_type != "legal_entity":
        raise NormativeControlError(
            "This version requires legal entity scope"
        )

    if parsed < date(2026, 7, 1):
        raise NormativeControlError(
            "2026 legal entity form not applicable to this month"
        )

    return dict(SOURCE)


def review_gross_paid_candidate(
    *,
    net_paid,
    pit_withheld_at_payment,
    levy_withheld_at_payment,
    candidate_gross_paid,
    posted_payment_verified,
    withholding_allocation_verified,
):
    """Arithmetic consistency, NOT normative attribution approval.

    Only payment-specific withholding is accepted here.
    Accrued tax and remitted tax are different concepts.
    """

    net = amount(net_paid, "net paid")
    pit = amount(
        pit_withheld_at_payment,
        "payment-linked PIT",
    )
    levy = amount(
        levy_withheld_at_payment,
        "payment-linked levy",
    )
    gross = amount(
        candidate_gross_paid,
        "candidate gross paid",
    )

    if posted_payment_verified is not True:
        raise NormativeControlError(
            "Actual posted payment not verified"
        )

    if withholding_allocation_verified is not True:
        raise NormativeControlError(
            "Independent withholding allocation not verified"
        )

    if gross != net + pit + levy:
        raise NormativeControlError(
            "Gross candidate does not reconcile to "
            "payment-specific withholding"
        )

    return {
        "net_paid": str(net),
        "payment_linked_pit": str(pit),
        "payment_linked_levy": str(levy),
        "gross_paid_candidate": str(gross),
        "arithmetic_reconciliation": "PASS",
        "statutory_gross_paid_approved": False,
        "income_category_approved": False,
        "correction_rules_approved": False,
        "official_generation": "BLOCKED",
    }


def review_d6_applicability(
    *,
    special_service_positions,
    eligibility_evidence_verified,
):
    """No D6 rows without a verified statutory basis."""

    if special_service_positions is None:
        return {
            "status": "APPLICABILITY_UNKNOWN",
            "d6_generation": "BLOCKED",
            "official_generation": "BLOCKED",
        }

    if type(special_service_positions) is not int:
        raise NormativeControlError(
            "Invalid special-service position count"
        )

    if special_service_positions < 0:
        raise NormativeControlError(
            "Negative special-service position count"
        )

    if eligibility_evidence_verified is not True:
        return {
            "status": "ELIGIBILITY_NOT_VERIFIED",
            "d6_generation": "BLOCKED",
            "official_generation": "BLOCKED",
        }

    if special_service_positions == 0:
        return {
            "status": "NO_SPECIAL_SERVICE_POSITIONS",
            "d6_generation": "NOT_APPLICABLE",
            "official_generation": "BLOCKED",
        }

    return {
        "status": "SPECIAL_SERVICE_REVIEW_REQUIRED",
        "d6_generation": "BLOCKED",
        "official_generation": "BLOCKED",
    }


def build_normative_review(
    *,
    company_id,
    payroll_period_id,
    month,
    d6_special_service_positions=None,
    d6_eligibility_evidence_verified=False,
    gross_paid_control=None,
):
    """Deterministic review, no unapproved statutory claims."""

    positive(company_id, "company_id")
    positive(payroll_period_id, "payroll_period_id")

    version = applicable_version(
        month, entity_type="legal_entity"
    )

    d6 = review_d6_applicability(
        special_service_positions=d6_special_service_positions,
        eligibility_evidence_verified=(
            d6_eligibility_evidence_verified
        ),
    )

    if gross_paid_control is not None:
        if not isinstance(gross_paid_control, dict):
            raise NormativeControlError(
                "Invalid gross paid control"
            )

        gross = review_gross_paid_candidate(
            **gross_paid_control
        )
    else:
        gross = {
            "status": "PAYMENT_ATTRIBUTION_NOT_SUPPLIED",
            "statutory_gross_paid_approved": False,
            "official_generation": "BLOCKED",
        }

    body = {
        "schema": SCHEMA,
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "reporting_month": month,
        "normative_source": version,
        "four_df": gross,
        "d6": d6,
        "normative_field_mapping_approved": False,
        "correction_rules_approved": False,
        "review_only": True,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }

    return {
        "body": body,
        "review_sha256": sha256(body),
    }


def verify_normative_review(review):
    if (
        type(review) is not dict
        or set(review) != {"body", "review_sha256"}
        or type(review["body"]) is not dict
    ):
        raise NormativeControlError(
            "Invalid normative review envelope"
        )

    body = review["body"]

    if (
        body.get("schema") != SCHEMA
        or body.get("review_only") is not True
        or body.get("official_generation") != "BLOCKED"
        or body.get("normative_field_mapping_approved")
            is not False
        or body.get("correction_rules_approved")
            is not False
        or body.get("official_rows_generated") != 0
        or type(body.get("official_rows_generated")) is not int
    ):
        raise NormativeControlError(
            "Unapproved statutory generation claim"
        )

    if body.get("normative_source") != applicable_version(
        body.get("reporting_month"),
        entity_type="legal_entity",
    ):
        raise NormativeControlError(
            "Normative source mismatch"
        )

    if (
        not isinstance(body.get("four_df"), dict)
        or body["four_df"].get(
            "statutory_gross_paid_approved"
        ) is not False
        or body["four_df"].get("official_generation")
            != "BLOCKED"
    ):
        raise NormativeControlError(
            "Unapproved 4DF gross-paid claim"
        )

    if (
        not isinstance(body.get("d6"), dict)
        or body["d6"].get("official_generation")
            != "BLOCKED"
        or body["d6"].get("d6_generation")
            not in ("BLOCKED", "NOT_APPLICABLE")
    ):
        raise NormativeControlError(
            "Unapproved D6 generation claim"
        )

    if review["review_sha256"] != sha256(body):
        raise NormativeControlError(
            "Normative review fingerprint mismatch"
        )

    return True
