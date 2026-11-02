"""Fail-closed D1/4DF payroll correction-to-report financial control.

Checks actual persisted financial revision evidence, not official
statutory form field allocation or submission authorization.
"""

from decimal import Decimal, InvalidOperation

from app.services.payroll_regulatory_financial_reconciliation import (
    FinancialReconciliationError,
)
from app.services.payroll_regulatory_revision_chain_review import (
    review_persisted_revision_chain,
)
from app.services.payroll_regulatory_active_financial_review import (
    review_persisted_active_financials,
)
from app.services.payroll_regulatory_persisted_correction_delta import (
    review_persisted_correction_delta,
)


REPORTS = ("d1", "4df")
COMPONENTS = (
    "personal_income_tax",
    "military_levy",
    "unified_social_contribution",
)


def decimal_amount(value):
    try:
        if isinstance(value, bool):
            raise ValueError("Boolean financial amount")
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise ValueError("Nonfinite financial amount")
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Unsupported financial precision")
        return amount
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FinancialReconciliationError(
            "Invalid statutory financial amount"
        ) from exc


def evaluate_correction_report_control(
    *,
    chain,
    active,
    delta,
    company_id,
    payroll_period_id,
    report_kind,
):
    if type(company_id) is not int or company_id <= 0:
        raise FinancialReconciliationError("Invalid company")

    if type(payroll_period_id) is not int or payroll_period_id <= 0:
        raise FinancialReconciliationError("Invalid period")

    if report_kind not in REPORTS:
        raise FinancialReconciliationError("Unsupported report")

    if not all(isinstance(row, dict) for row in (chain, active, delta)):
        raise FinancialReconciliationError(
            "Missing report financial evidence"
        )

    failures = []

    if chain.get("status") != "REVISION_CHAIN_VERIFIED":
        failures.append("REVISION_CHAIN_NOT_VERIFIED")

    if chain.get("data_origin") != "PERSISTED_POSTGRESQL":
        failures.append("CHAIN_NOT_PERSISTED")

    if active.get("status") != "ACTIVE_FINANCIAL_RECONCILED":
        failures.append("ACTIVE_FINANCIALS_NOT_RECONCILED")

    if active.get("data_origin") != "PERSISTED_POSTGRESQL":
        failures.append("ACTIVE_FINANCIALS_NOT_PERSISTED")

    if delta.get("status") != "CORRECTION_COMPONENT_DELTA_RECONCILED":
        failures.append("CORRECTION_DELTA_NOT_RECONCILED")

    if delta.get("persisted_correction_delta_verified") is not True:
        failures.append("CORRECTION_DELTA_NOT_PERSISTED")

    if delta.get("correction_chain_provenance") != "PERSISTED_POSTGRESQL":
        failures.append("CORRECTION_CHAIN_PROVENANCE_MISSING")

    for name, row in (
        ("CHAIN", chain),
        ("ACTIVE", active),
        ("DELTA", delta),
    ):
        if row.get("company_id") != company_id:
            failures.append(name + "_COMPANY_MISMATCH")
        if row.get("payroll_period_id") != payroll_period_id:
            failures.append(name + "_PERIOD_MISMATCH")
        if row.get("official_generation") != "BLOCKED":
            failures.append(name + "_UNSAFE_GENERATION_STATE")

    active_ids = chain.get("active_calculation_ids", [])
    historical_ids = chain.get("historical_calculation_ids", [])

    if (
        not isinstance(active_ids, list)
        or not isinstance(historical_ids, list)
        or not active_ids
        or not historical_ids
    ):
        failures.append("REAL_REPLACEMENT_CHAIN_REQUIRED")
        active_ids = []
        historical_ids = []

    if (
        delta.get("active_calculation_ids") != active_ids
        or delta.get("historical_calculation_ids") != historical_ids
    ):
        failures.append("CORRECTION_REVISION_SCOPE_MISMATCH")

    if (
        active.get("active_calculation_count") != len(active_ids)
        or active.get("historical_calculation_count")
        != len(historical_ids)
    ):
        failures.append("ACTIVE_REVISION_COUNT_MISMATCH")

    rows = delta.get("components")
    if not isinstance(rows, list) or not rows:
        failures.append("MISSING_DELTA_COMPONENTS")
        rows = []

    seen = set()
    verified_components = []

    for row in rows:
        if not isinstance(row, dict):
            failures.append("INVALID_DELTA_ROW")
            continue

        component = row.get("component")
        currency = row.get("currency")

        if component not in COMPONENTS or currency != "UAH":
            failures.append("UNSUPPORTED_COMPONENT_OR_CURRENCY")
            continue

        key = (component, currency)
        if key in seen:
            failures.append("DUPLICATE_COMPONENT")
            continue

        seen.add(key)

        try:
            original_base = decimal_amount(row.get("original_base"))
            replacement_base = decimal_amount(row.get("replacement_base"))
            base_delta = decimal_amount(row.get("base_delta"))
            original_tax = decimal_amount(row.get("original_tax"))
            replacement_tax = decimal_amount(row.get("replacement_tax"))
            tax_delta = decimal_amount(row.get("tax_delta"))
            original_sources = decimal_amount(row.get("original_sources"))
            replacement_sources = decimal_amount(
                row.get("replacement_sources")
            )

            if (
                replacement_base - original_base != base_delta
                or replacement_tax - original_tax != tax_delta
                or original_base != original_sources
                or replacement_base != replacement_sources
            ):
                failures.append("COMPONENT_FINANCIAL_MISMATCH")
                continue

            verified_components.append(component)

        except FinancialReconciliationError:
            failures.append("INVALID_COMPONENT_FINANCIAL_AMOUNT")

    if delta.get("missing_source_line_ids"):
        failures.append("MISSING_DATED_SOURCE")

    if delta.get("mismatched_source_line_ids"):
        failures.append("DATED_SOURCE_MISMATCH")

    if active.get("errors"):
        failures.append("ACTIVE_FINANCIAL_ERRORS")

    try:
        if decimal_amount(active.get("active_base_total")) != (
            decimal_amount(active.get("active_allocated_total"))
        ):
            failures.append("ACTIVE_ALLOCATION_TOTAL_MISMATCH")
    except FinancialReconciliationError:
        failures.append("INVALID_ACTIVE_TOTAL")

    if set(verified_components) != set(COMPONENTS):
        failures.append("INCOMPLETE_TAX_COMPONENT_COVERAGE")

    financial_verified = not failures

    return {
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "report_kind": report_kind,
        "financial_status": (
            "REPORT_CORRECTION_FINANCIALS_RECONCILED"
            if financial_verified else "NOT_VERIFIED"
        ),
        "active_calculation_ids": active_ids,
        "excluded_historical_calculation_ids": historical_ids,
        "verified_components": sorted(verified_components),
        "failures": sorted(set(failures)),
        "financial_correction_verified": financial_verified,
        "official_form_fields_verified": False,
        "immutable_report_snapshot_verified": False,
        "official_generation": "BLOCKED",
    }


async def review_persisted_correction_report(
    db,
    *,
    company_id,
    payroll_period_id,
    report_kind,
):
    if report_kind not in REPORTS:
        raise FinancialReconciliationError("Unsupported report kind")

    chain = await review_persisted_revision_chain(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    if (
        chain.get("status") != "REVISION_CHAIN_VERIFIED"
        or not chain.get("historical_calculation_ids")
    ):
        return {
            "company_id": company_id,
            "payroll_period_id": payroll_period_id,
            "report_kind": report_kind,
            "financial_status": "NOT_VERIFIED",
            "financial_correction_verified": False,
            "failures": ["NO_VERIFIED_PERSISTED_CORRECTION_CHAIN"],
            "official_generation": "BLOCKED",
        }

    active = await review_persisted_active_financials(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    delta = await review_persisted_correction_delta(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    return evaluate_correction_report_control(
        chain=chain,
        active=active,
        delta=delta,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        report_kind=report_kind,
    )
