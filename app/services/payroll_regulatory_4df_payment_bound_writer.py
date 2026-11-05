"""Source-bound 4DF posted payment evidence in internal snapshots.

This is internal review, never an official statutory filing.
Every financial evidence item must first pass the existing real
posted payment and tax remittance verifier.

The saved 4DF row contains a canonical bounded string with
payment/remittance identifiers, dates, amounts and SHA256.

Caller owns the DB transaction.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json

from sqlalchemy import text

from app.services.payroll_regulatory_payment_evidence import (
    verify_4df_posted_payment_evidence,
)

from app.services.payroll_regulatory_verified_gross_writer import (
    derive_independent_gross_controls,
    persist_gross_verified_internal_four_form_review,
)

from app.services.payroll_regulatory_internal_reconciliation import (
    InternalSnapshot,
    verify_internal_snapshot,
)


KEY = "fourdf_posted_payment_evidence"
SCHEMA = "erp.4df-posted-payment.v1"


class FourDFPaymentBindingError(ValueError):
    pass


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def money(value):
    if value is None or isinstance(value, (bool, float)):
        raise FourDFPaymentBindingError("Unsafe payment amount")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise FourDFPaymentBindingError(
            "Invalid payment amount"
        ) from None
    if not number.is_finite() or number < 0:
        raise FourDFPaymentBindingError(
            "Invalid payment amount"
        )
    if number != number.quantize(Decimal("0.01")):
        raise FourDFPaymentBindingError(
            "Invalid payment precision"
        )
    return f"{number:.2f}"


def positive(value, label):
    if type(value) is not int or value <= 0:
        raise FourDFPaymentBindingError(
            f"Invalid {label}"
        )
    return value


def build_anchor(calculation_id, employee_id, allocations):
    positive(calculation_id, "calculation id")
    positive(employee_id, "employee id")

    if not isinstance(allocations, list) or not allocations:
        raise FourDFPaymentBindingError(
            "Verified payment allocations required"
        )

    items = []
    seen_payments = set()
    seen_remittances = set()

    for allocation in allocations:
        if not isinstance(allocation, dict):
            raise FourDFPaymentBindingError(
                "Invalid allocation evidence"
            )

        if allocation.get("payroll_calculation_id") != calculation_id:
            raise FourDFPaymentBindingError(
                "Calculation mismatch"
            )

        if allocation.get("_verified_employee_id") != employee_id:
            raise FourDFPaymentBindingError(
                "Employee mismatch"
            )

        when = allocation.get("payment_date")
        if type(when) is not date:
            raise FourDFPaymentBindingError(
                "Verified payment date required"
            )

        payments = allocation.get("payroll_disbursement_ids")
        taxes = allocation.get("tax_remittance_line_ids")

        for refs, label in (
            (payments, "payment"),
            (taxes, "remittance"),
        ):
            if (
                not isinstance(refs, list)
                or not refs
                or len(refs) != len(set(refs))
            ):
                raise FourDFPaymentBindingError(
                    f"Invalid {label} references"
                )
            for ref in refs:
                positive(ref, label)

        if seen_payments.intersection(payments):
            raise FourDFPaymentBindingError(
                "Duplicate payment evidence"
            )

        if seen_remittances.intersection(taxes):
            raise FourDFPaymentBindingError(
                "Duplicate remittance evidence"
            )

        seen_payments.update(payments)
        seen_remittances.update(taxes)

        if allocation.get("payment_evidence_reference") != (
            "payroll_disbursements:"
            + ",".join(map(str, sorted(payments)))
        ):
            raise FourDFPaymentBindingError(
                "Unverified payment reference"
            )

        if allocation.get("tax_remittance_evidence_reference") != (
            "payroll_tax_remittance_lines:"
            + ",".join(map(str, sorted(taxes)))
        ):
            raise FourDFPaymentBindingError(
                "Unverified remittance reference"
            )

        items.append({
            "d": when.isoformat(),
            "p": sorted(payments),
            "t": sorted(taxes),
            "a": money(allocation.get("paid_amount")),
            "i": money(allocation.get("pit_remitted")),
            "l": money(allocation.get("levy_remitted")),
        })

    items.sort(key=canonical)

    body = {
        "v": SCHEMA,
        "c": calculation_id,
        "e": employee_id,
        "items": items,
    }

    digest = hashlib.sha256(
        canonical(body).encode("utf-8")
    ).hexdigest()

    return canonical({**body, "sha256": digest})


def verify_anchor(value):
    if not isinstance(value, str) or not value:
        raise FourDFPaymentBindingError(
            "Payment evidence string missing"
        )

    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        raise FourDFPaymentBindingError(
            "Malformed payment evidence"
        ) from None

    if (
        not isinstance(data, dict)
        or set(data) != {"v", "c", "e", "items", "sha256"}
        or data["v"] != SCHEMA
        or canonical(data) != value
    ):
        raise FourDFPaymentBindingError(
            "Invalid canonical payment evidence"
        )

    positive(data["c"], "calculation")
    positive(data["e"], "employee")

    items = data["items"]
    if not isinstance(items, list) or not items:
        raise FourDFPaymentBindingError(
            "Empty payment evidence"
        )

    payment_ids = set()
    remittance_ids = set()

    for item in items:
        if (
            not isinstance(item, dict)
            or set(item) != {"d", "p", "t", "a", "i", "l"}
        ):
            raise FourDFPaymentBindingError(
                "Invalid payment evidence item"
            )

        try:
            parsed = date.fromisoformat(item["d"])
        except (TypeError, ValueError):
            raise FourDFPaymentBindingError(
                "Invalid evidence date"
            ) from None

        if parsed.isoformat() != item["d"]:
            raise FourDFPaymentBindingError(
                "Noncanonical evidence date"
            )

        for field, seen in (
            ("p", payment_ids),
            ("t", remittance_ids),
        ):
            refs = item[field]
            if (
                not isinstance(refs, list)
                or not refs
                or refs != sorted(set(refs))
            ):
                raise FourDFPaymentBindingError(
                    "Invalid evidence references"
                )
            for ref in refs:
                positive(ref, field)
                if ref in seen:
                    raise FourDFPaymentBindingError(
                        "Duplicate evidence reference"
                    )
                seen.add(ref)

        for field in ("a", "i", "l"):
            if item[field] != money(item[field]):
                raise FourDFPaymentBindingError(
                    "Noncanonical amount"
                )

    if items != sorted(items, key=canonical):
        raise FourDFPaymentBindingError(
            "Noncanonical payment ordering"
        )

    body = {key: data[key] for key in (
        "v", "c", "e", "items"
    )}

    expected = hashlib.sha256(
        canonical(body).encode("utf-8")
    ).hexdigest()

    if not (
        isinstance(data["sha256"], str)
        and hmac.compare_digest(data["sha256"], expected)
    ):
        raise FourDFPaymentBindingError(
            "Payment evidence digest mismatch"
        )

    return data


async def bind_4df_posted_payment_evidence(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
    reviewed_allocations,
):
    """Check real posted sources, then bind to every 4DF source row."""

    independent = await derive_independent_gross_controls(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
    )

    if (
        not isinstance(reviewed_allocations, list)
        or not reviewed_allocations
    ):
        raise FourDFPaymentBindingError(
            "Payment allocations required"
        )

    allocations = deepcopy(reviewed_allocations)

    sources = (
        await db.execute(
            text("""
                SELECT c.id AS calculation_id,
                       e.id AS employee_id
                FROM payroll_calculations c
                JOIN employment_contracts ec
                  ON ec.id = c.employment_contract_id
                 AND ec.company_id = c.company_id
                JOIN employees e
                  ON e.id = ec.employee_id
                 AND e.company_id = ec.company_id
                WHERE c.company_id = :cid
                  AND c.payroll_period_id = :pid
                FOR UPDATE OF c
            """),
            {"cid": company_id, "pid": payroll_period_id},
        )
    ).mappings().all()

    employees = {}

    for source in sources:
        calc_id = source["calculation_id"]
        if calc_id in employees:
            raise FourDFPaymentBindingError(
                "Ambiguous payroll source"
            )
        employees[calc_id] = source["employee_id"]

    groups = {}

    for allocation in allocations:
        if not isinstance(allocation, dict):
            raise FourDFPaymentBindingError(
                "Invalid allocation"
            )

        calc_id = allocation.get("payroll_calculation_id")

        if calc_id not in employees:
            raise FourDFPaymentBindingError(
                "Unknown or cross-company payroll calculation"
            )

        employee_id = employees[calc_id]

        if (
            "employee_id" in allocation
            and allocation["employee_id"] != employee_id
        ):
            raise FourDFPaymentBindingError(
                "Payroll employee mismatch"
            )

        allocation["_verified_employee_id"] = employee_id
        groups.setdefault(calc_id, []).append(allocation)

    # This is the existing independent PostgreSQL verifier.
    # It rejects unposted/reversed journals and mismatched
    # paid/PIT/levy totals, dates and duplicated source IDs.
    await verify_4df_posted_payment_evidence(
        db,
        company_id=company_id,
        reviewed_allocations=allocations,
    )

    anchors = {
        calc_id: build_anchor(
            calc_id,
            employees[calc_id],
            group,
        )
        for calc_id, group in groups.items()
    }

    report_id = report_references["4DF"]

    rows = (
        await db.execute(
            text("""
                SELECT id, row_code, payload_json,
                       payroll_calculation_id
                FROM payroll_regulatory_report_rows
                WHERE company_id = :cid
                  AND payroll_regulatory_report_id = :rid
                ORDER BY row_no
                FOR UPDATE
            """),
            {"cid": company_id, "rid": report_id},
        )
    ).mappings().all()

    if len(rows) != len(anchors):
        raise FourDFPaymentBindingError(
            "Incomplete 4DF payment source coverage"
        )

    seen = set()
    prepared = []

    for row in rows:
        if row["row_code"] != "ERP_INTERNAL_RECONCILIATION_V1":
            raise FourDFPaymentBindingError(
                "Unsupported internal row"
            )

        try:
            payload = json.loads(row["payload_json"])
        except (ValueError, TypeError):
            raise FourDFPaymentBindingError(
                "Invalid 4DF source JSON"
            ) from None

        if (
            payload.get("schema")
            != "erp.internal-regulatory-row.v1"
            or payload.get("form") != "4DF"
            or payload.get("company_id") != company_id
            or payload.get("payroll_period") != payroll_period
        ):
            raise FourDFPaymentBindingError(
                "4DF envelope mismatch"
            )

        calc_id = payload.get("payroll_calculation_id")

        if (
            calc_id not in anchors
            or calc_id in seen
            or row["payroll_calculation_id"] != calc_id
            or payload.get("source", {}).get("type")
                != "payroll_calculation"
            or payload["source"].get("id") != calc_id
        ):
            raise FourDFPaymentBindingError(
                "4DF calculation identity mismatch"
            )

        seen.add(calc_id)

        attrs = payload.get("attributes")
        if not isinstance(attrs, dict):
            raise FourDFPaymentBindingError(
                "4DF attributes missing"
            )

        anchor = anchors[calc_id]
        previous = attrs.get(KEY)

        if previous is not None:
            verify_anchor(previous)

            if previous != anchor:
                raise FourDFPaymentBindingError(
                    "Existing 4DF payment evidence differs"
                )

        updated = dict(payload)
        updated_attrs = dict(attrs)
        updated_attrs[KEY] = anchor
        updated["attributes"] = updated_attrs

        prepared.append((row["id"], canonical(updated)))

    if seen != set(anchors):
        raise FourDFPaymentBindingError(
            "Missing 4DF source evidence"
        )

    for row_id, payload in prepared:
        await db.execute(
            text("""
                UPDATE payroll_regulatory_report_rows
                SET payload_json = :payload
                WHERE company_id = :cid
                  AND payroll_regulatory_report_id = :rid
                  AND id = :row_id
            """),
            {
                "payload": payload,
                "cid": company_id,
                "rid": report_id,
                "row_id": row_id,
            },
        )

    return anchors


async def persist_4df_payment_bound_internal_review(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
    reviewed_allocations,
    request_key,
):
    anchors = await bind_4df_posted_payment_evidence(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
        reviewed_allocations=reviewed_allocations,
    )

    result = await persist_gross_verified_internal_four_form_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
        request_key=request_key,
    )

    stored = (
        await db.execute(
            text("""
                SELECT version, payload_json, snapshot_sha256
                FROM payroll_regulatory_internal_snapshots
                WHERE company_id = :cid
                  AND payroll_period_id = :pid
                  AND id = :sid
            """),
            {
                "cid": company_id,
                "pid": payroll_period_id,
                "sid": result["id"],
            },
        )
    ).mappings().one()

    payload = verify_internal_snapshot(
        InternalSnapshot(
            company_id=company_id,
            payroll_period=payroll_period,
            version=stored["version"],
            canonical_json=stored["payload_json"],
            sha256=stored["snapshot_sha256"].strip(),
        )
    )

    rows = [
        row for row in payload["rows"]
        if row["form"] == "4DF"
    ]

    if len(rows) != len(anchors):
        raise FourDFPaymentBindingError(
            "Persisted 4DF evidence coverage mismatch"
        )

    seen = set()

    for row in rows:
        calc_id = row["source_id"]

        if calc_id not in anchors or calc_id in seen:
            raise FourDFPaymentBindingError(
                "Persisted 4DF source mismatch"
            )

        seen.add(calc_id)
        actual = row["attributes"].get(KEY)
        verify_anchor(actual)

        if actual != anchors[calc_id]:
            raise FourDFPaymentBindingError(
                "Persisted payment evidence changed"
            )

    if result.get("official_generation") != "BLOCKED":
        raise FourDFPaymentBindingError(
            "Unexpected statutory authorization"
        )

    return {
        **result,
        "fourdf_posted_payment_evidence_in_snapshot": True,
        "fourdf_payment_source_count": len(anchors),
        "payment_evidence_verified": True,
        "tax_remittance_evidence_verified": True,
        "official_generation": "BLOCKED",
    }
