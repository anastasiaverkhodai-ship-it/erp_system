"""Financial correction projection for D1 and 4DF.

This is a source-bound internal evidence projection, NOT official
form-field allocation and NOT a persisted immutable report snapshot.

The integrity digest verifies that the supplied evidence has not changed.
It does not independently certify the original source or authorize filing.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json

from app.services.payroll_regulatory_correction_report_control import (
    review_persisted_correction_report,
)
from app.services.payroll_regulatory_persisted_correction_delta import (
    review_persisted_correction_delta,
)
from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)

MONEY = Decimal("0.01")
COMPONENTS = frozenset({
    "personal_income_tax",
    "military_levy",
    "unified_social_contribution",
})


def parse_money(value):
    if value is None or isinstance(value, bool):
        raise FinancialReconciliationError("Invalid amount")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.quantize(MONEY):
            raise ValueError("Invalid monetary precision")
        return number
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FinancialReconciliationError("Invalid amount") from exc


def canonical_bytes(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def seal_projection(payload):
    return {
        "algorithm": "sha256",
        "digest": hashlib.sha256(canonical_bytes(payload)).hexdigest(),
        "payload": json.loads(canonical_bytes(payload)),
        "persistent_immutability_verified": False,
        "official_generation": "BLOCKED",
    }


def verify_projection_seal(seal):
    if not isinstance(seal, dict):
        return False
    if seal.get("algorithm") != "sha256":
        return False
    if seal.get("persistent_immutability_verified") is not False:
        return False
    if seal.get("official_generation") != "BLOCKED":
        return False
    digest = seal.get("digest")
    payload = seal.get("payload")
    if not isinstance(digest, str) or len(digest) != 64:
        return False
    if not isinstance(payload, dict):
        return False
    try:
        return hashlib.sha256(canonical_bytes(payload)).hexdigest() == digest
    except (TypeError, ValueError, OverflowError):
        return False


def build_correction_projection(control, delta):
    if not isinstance(control, dict) or not isinstance(delta, dict):
        raise FinancialReconciliationError("Invalid projection sources")

    company = control.get("company_id")
    period = control.get("payroll_period_id")
    report = control.get("report_kind")

    if type(company) is not int or company <= 0:
        raise FinancialReconciliationError("Invalid company")
    if type(period) is not int or period <= 0:
        raise FinancialReconciliationError("Invalid period")
    if report not in ("d1", "4df"):
        raise FinancialReconciliationError("Unsupported report")

    if (
        control.get("financial_status")
        != "REPORT_CORRECTION_FINANCIALS_RECONCILED"
        or control.get("financial_correction_verified") is not True
        or control.get("official_form_fields_verified") is not False
        or control.get("official_generation") != "BLOCKED"
        or control.get("failures")
    ):
        raise FinancialReconciliationError(
            "Correction financial control is not verified"
        )

    if (
        delta.get("company_id") != company
        or delta.get("payroll_period_id") != period
        or delta.get("status") != "CORRECTION_COMPONENT_DELTA_RECONCILED"
        or delta.get("persisted_correction_delta_verified") is not True
        or delta.get("correction_chain_provenance")
        != "PERSISTED_POSTGRESQL"
        or delta.get("official_generation") != "BLOCKED"
        or delta.get("missing_source_line_ids")
        or delta.get("mismatched_source_line_ids")
    ):
        raise FinancialReconciliationError(
            "Correction delta source is not verified"
        )

    active = control.get("active_calculation_ids")
    historical = control.get("excluded_historical_calculation_ids")

    if (
        not isinstance(active, list)
        or not isinstance(historical, list)
        or not active or not historical
        or any(type(i) is not int or i <= 0 for i in active + historical)
        or len(set(active + historical)) != len(active + historical)
        or delta.get("active_calculation_ids") != active
        or delta.get("historical_calculation_ids") != historical
    ):
        raise FinancialReconciliationError("Unsafe revision partition")

    rows = delta.get("components")
    if not isinstance(rows, list) or len(rows) != 3:
        raise FinancialReconciliationError("Incomplete tax components")

    components = []
    seen = set()

    for row in rows:
        if not isinstance(row, dict):
            raise FinancialReconciliationError("Invalid component")

        name = row.get("component")
        currency = row.get("currency")
        if name not in COMPONENTS or name in seen or currency != "UAH":
            raise FinancialReconciliationError(
                "Unsupported or duplicate component"
            )
        seen.add(name)

        amounts = {
            key: parse_money(row.get(key))
            for key in (
                "original_base",
                "replacement_base",
                "base_delta",
                "original_tax",
                "replacement_tax",
                "tax_delta",
                "original_sources",
                "replacement_sources",
            )
        }

        if (
            amounts["replacement_base"] - amounts["original_base"]
            != amounts["base_delta"]
            or amounts["replacement_tax"] - amounts["original_tax"]
            != amounts["tax_delta"]
            or amounts["original_sources"] != amounts["original_base"]
            or amounts["replacement_sources"] != amounts["replacement_base"]
        ):
            raise FinancialReconciliationError("Financial delta mismatch")

        components.append({
            "component": name,
            "currency": "UAH",
            **{key: str(value) for key, value in amounts.items()},
        })

    if seen != COMPONENTS:
        raise FinancialReconciliationError("Incomplete component set")

    components.sort(key=lambda row: row["component"])

    return {
        "schema": "payroll_internal_correction_projection_v1",
        "company_id": company,
        "payroll_period_id": period,
        "report_kind": report,
        "active_calculation_ids": sorted(active),
        "excluded_historical_calculation_ids": sorted(historical),
        "components": components,
        "source_provenance": "PERSISTED_POSTGRESQL",
        "official_form_fields_verified": False,
        "official_generation": "BLOCKED",
    }


async def review_and_seal_persisted_correction(
    db, *, company_id, payroll_period_id, report_kind
):
    control = await review_persisted_correction_report(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        report_kind=report_kind,
    )
    if control.get("financial_correction_verified") is not True:
        return {
            "status": "NOT_VERIFIED",
            "failures": control.get("failures", ["UNVERIFIED_CORRECTION"]),
            "official_generation": "BLOCKED",
        }

    delta = await review_persisted_correction_delta(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    projection = build_correction_projection(control, delta)
    sealed = seal_projection(projection)

    if not verify_projection_seal(sealed):
        raise FinancialReconciliationError("Projection integrity failure")

    return {
        "status": "INTERNAL_FINANCIAL_PROJECTION_VERIFIED",
        "seal": sealed,
        "database_snapshot_persisted": False,
        "official_generation": "BLOCKED",
    }
