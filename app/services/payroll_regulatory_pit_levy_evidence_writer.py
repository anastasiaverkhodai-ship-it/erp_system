"""Independently verified PIT and levy source evidence.

This module reads frozen statutory component lines, verifies
them against independently queried persisted payroll records,
and binds their identifiers and amounts to a D1 internal row.

The resulting snapshot remains review-only. Posted remittances
are independently proven by the existing 4DF payment binder.
Accrual does not imply full remittance.
"""

import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation

from sqlalchemy import text

from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_persisted_source_verifier import (
    verify_persisted_payroll_sources,
)
from app.services.payroll_regulatory_source_reconciliation import (
    reconcile_regulatory_sources,
)


KEY = "pit_levy_statutory_evidence"
SCHEMA = "erp.pit-levy-statutory-evidence.v1"
PIT = "personal_income_tax"
LEVY = "military_levy"


class PITLevyEvidenceError(ValueError):
    pass


def canonical(data):
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def amount(value):
    if value is None or isinstance(value, (bool, float)):
        raise PITLevyEvidenceError(
            "Unsafe statutory amount"
        )

    try:
        number = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise PITLevyEvidenceError(
            "Invalid statutory amount"
        ) from None

    if (
        not number.is_finite()
        or number < 0
        or number.as_tuple().exponent < -2
        and number != number.quantize(Decimal("0.01"))
    ):
        raise PITLevyEvidenceError(
            "Invalid statutory amount or precision"
        )

    return f"{number.quantize(Decimal('0.01')):.2f}"


def positive(value, label):
    if type(value) is not int or value <= 0:
        raise PITLevyEvidenceError(
            f"Invalid {label}"
        )
    return value


def make_anchor(
    *,
    company_id,
    payroll_period_id,
    source,
):
    positive(company_id, "company")
    positive(payroll_period_id, "period")

    if not isinstance(source, dict):
        raise PITLevyEvidenceError(
            "Statutory payroll source required"
        )

    calculation_id = positive(
        source.get("payroll_calculation_id"),
        "calculation",
    )

    employee_id = positive(
        source.get("employee_id"),
        "employee",
    )

    revision = positive(
        source.get("revision"),
        "calculation revision",
    )

    if source.get("issues"):
        raise PITLevyEvidenceError(
            "Unresolved statutory source issues"
        )

    if source.get("currency_code") != "UAH":
        raise PITLevyEvidenceError(
            "Expected UAH statutory evidence"
        )

    components = source.get("components")

    if not isinstance(components, list):
        raise PITLevyEvidenceError(
            "Frozen statutory components required"
        )

    lines = []
    used = set()
    totals = {PIT: Decimal("0.00"), LEVY: Decimal("0.00")}
    categories = set()

    for component in components:
        if not isinstance(component, dict):
            raise PITLevyEvidenceError(
                "Invalid statutory component"
            )

        category = component.get("component")

        if category not in (PIT, LEVY):
            continue

        line_id = positive(
            component.get("source_line_id"),
            "statutory line ID",
        )

        if line_id in used:
            raise PITLevyEvidenceError(
                "Duplicate statutory line ID"
            )

        if component.get("currency_code") != "UAH":
            raise PITLevyEvidenceError(
                "Statutory component currency mismatch"
            )

        used.add(line_id)
        categories.add(category)

        value = amount(component.get("amount"))
        base = amount(component.get("base_amount"))

        totals[category] += Decimal(value)

        lines.append({
            "id": line_id,
            "component": category,
            "amount": value,
            "base": base,
        })

    if categories != {PIT, LEVY}:
        raise PITLevyEvidenceError(
            "Both frozen PIT and levy components required"
        )

    withheld = amount(
        source.get("employee_withholding_amount")
    )

    if totals[PIT] + totals[LEVY] != Decimal(withheld):
        raise PITLevyEvidenceError(
            "PIT and levy do not reconcile to withholding"
        )

    lines.sort(key=lambda line: (
        line["component"], line["id"]
    ))

    body = {
        "schema": SCHEMA,
        "company": company_id,
        "period": payroll_period_id,
        "calculation": calculation_id,
        "employee": employee_id,
        "revision": revision,
        "currency": "UAH",
        "pit": amount(totals[PIT]),
        "levy": amount(totals[LEVY]),
        "withheld": withheld,
        "lines": lines,
        "official": "BLOCKED",
    }

    digest = hashlib.sha256(
        canonical(body).encode("utf-8")
    ).hexdigest()

    return canonical({
        **body,
        "sha256": digest,
    })


def verify_anchor(value):
    if not isinstance(value, str) or not value:
        raise PITLevyEvidenceError(
            "Statutory evidence string missing"
        )

    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        raise PITLevyEvidenceError(
            "Malformed statutory evidence"
        ) from None

    fields = {
        "schema", "company", "period", "calculation",
        "employee", "revision", "currency", "pit",
        "levy", "withheld", "lines", "official", "sha256",
    }

    if (
        not isinstance(data, dict)
        or set(data) != fields
        or canonical(data) != value
        or data["schema"] != SCHEMA
        or data["currency"] != "UAH"
        or data["official"] != "BLOCKED"
    ):
        raise PITLevyEvidenceError(
            "Invalid statutory evidence schema"
        )

    for field in (
        "company", "period", "calculation",
        "employee", "revision",
    ):
        positive(data[field], field)

    for field in ("pit", "levy", "withheld"):
        if data[field] != amount(data[field]):
            raise PITLevyEvidenceError(
                "Noncanonical statutory amount"
            )

    if (
        Decimal(data["pit"]) + Decimal(data["levy"])
        != Decimal(data["withheld"])
    ):
        raise PITLevyEvidenceError(
            "Statutory withholding does not reconcile"
        )

    lines = data["lines"]
    if not isinstance(lines, list) or not lines:
        raise PITLevyEvidenceError(
            "Missing statutory line evidence"
        )

    used = set()
    totals = {PIT: Decimal("0.00"), LEVY: Decimal("0.00")}
    categories = set()

    for line in lines:
        if not isinstance(line, dict) or set(line) != {
            "id", "component", "amount", "base"
        }:
            raise PITLevyEvidenceError(
                "Malformed statutory line evidence"
            )

        line_id = positive(line["id"], "line ID")

        if line_id in used:
            raise PITLevyEvidenceError(
                "Duplicate statutory line evidence"
            )

        used.add(line_id)

        category = line["component"]

        if category not in totals:
            raise PITLevyEvidenceError(
                "Unexpected statutory component"
            )

        categories.add(category)

        for field in ("amount", "base"):
            if line[field] != amount(line[field]):
                raise PITLevyEvidenceError(
                    "Invalid statutory line amount"
                )

        totals[category] += Decimal(line["amount"])

    if categories != {PIT, LEVY}:
        raise PITLevyEvidenceError(
            "Incomplete PIT/levy source evidence"
        )

    if lines != sorted(
        lines,
        key=lambda line: (
            line["component"], line["id"]
        ),
    ):
        raise PITLevyEvidenceError(
            "Noncanonical statutory line ordering"
        )

    if (
        amount(totals[PIT]) != data["pit"]
        or amount(totals[LEVY]) != data["levy"]
    ):
        raise PITLevyEvidenceError(
            "Statutory line totals do not reconcile"
        )

    body = {
        field: data[field]
        for field in fields
        if field != "sha256"
    }

    expected = hashlib.sha256(
        canonical(body).encode("utf-8")
    ).hexdigest()

    if (
        not isinstance(data["sha256"], str)
        or not hmac.compare_digest(
            expected,
            data["sha256"],
        )
    ):
        raise PITLevyEvidenceError(
            "Statutory evidence SHA256 mismatch"
        )

    return data


async def bind_independent_pit_levy_evidence(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
):
    """Requery source payroll and bind independent PIT/levy lines."""

    if (
        not isinstance(report_references, dict)
        or set(report_references)
        != {"D1", "4DF", "D5", "D6"}
    ):
        raise PITLevyEvidenceError(
            "Four report references required"
        )

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

    # This independently checks PIT + levy = withholding
    # and USC = employer contribution for every source.
    totals = reconcile_regulatory_sources(readiness)

    sources = readiness["calculation_sources"]

    if len(sources) != totals.source_count:
        raise PITLevyEvidenceError(
            "Incomplete statutory source coverage"
        )

    anchors = {}

    for source in sources:
        key = source["payroll_calculation_id"]

        if key in anchors:
            raise PITLevyEvidenceError(
                "Duplicate payroll calculation"
            )

        anchors[key] = make_anchor(
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            source=source,
        )

    if not anchors:
        raise PITLevyEvidenceError(
            "No statutory payroll evidence"
        )

    if (
        sum(
            Decimal(verify_anchor(value)["pit"])
            for value in anchors.values()
        ) != totals.pit
        or sum(
            Decimal(verify_anchor(value)["levy"])
            for value in anchors.values()
        ) != totals.military_levy
    ):
        raise PITLevyEvidenceError(
            "Independent PIT/levy totals mismatch"
        )

    report_id = report_references["D1"]

    header = (
        await db.execute(
            text("""
                SELECT id, status, report_kind
                FROM payroll_regulatory_reports
                WHERE company_id = :cid
                  AND payroll_period_id = :pid
                  AND id = :rid
                FOR UPDATE
            """),
            {
                "cid": company_id,
                "pid": payroll_period_id,
                "rid": report_id,
            },
        )
    ).mappings().one_or_none()

    if (
        header is None
        or header["status"] != "draft"
        or header["report_kind"] != "d1"
    ):
        raise PITLevyEvidenceError(
            "Invalid scoped D1 draft"
        )

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
        raise PITLevyEvidenceError(
            "Incomplete D1 statutory row coverage"
        )

    prepared = []
    observed = set()

    for row in rows:
        if row["row_code"] != (
            "ERP_INTERNAL_RECONCILIATION_V1"
        ):
            raise PITLevyEvidenceError(
                "Unsupported internal row"
            )

        try:
            payload = json.loads(row["payload_json"])
        except (ValueError, TypeError):
            raise PITLevyEvidenceError(
                "Invalid internal row JSON"
            ) from None

        if not isinstance(payload, dict):
            raise PITLevyEvidenceError(
                "Internal row object required"
            )

        calculation_id = payload.get(
            "payroll_calculation_id"
        )

        if (
            payload.get("schema")
                != "erp.internal-regulatory-row.v1"
            or payload.get("form") != "D1"
            or payload.get("company_id") != company_id
            or payload.get("payroll_period")
                != payroll_period
            or calculation_id not in anchors
            or calculation_id in observed
            or row["payroll_calculation_id"]
                != calculation_id
            or payload.get("source", {}).get("type")
                != "payroll_calculation"
            or payload["source"].get("id")
                != calculation_id
        ):
            raise PITLevyEvidenceError(
                "D1 statutory identity mismatch"
            )

        observed.add(calculation_id)

        attributes = payload.get("attributes")

        if not isinstance(attributes, dict):
            raise PITLevyEvidenceError(
                "Invalid internal attributes"
            )

        anchor = anchors[calculation_id]
        previous = attributes.get(KEY)

        if previous is not None:
            verify_anchor(previous)

            if previous != anchor:
                raise PITLevyEvidenceError(
                    "Statutory source evidence changed"
                )

        updated = dict(payload)
        attrs = dict(attributes)
        attrs[KEY] = anchor
        updated["attributes"] = attrs

        prepared.append((
            row["id"],
            canonical(updated),
        ))

    if observed != set(anchors):
        raise PITLevyEvidenceError(
            "Missing calculation evidence"
        )

    # No writes occur until all source rows are validated.
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
