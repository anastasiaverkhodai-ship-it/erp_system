"""Strict read bridge for persisted D1/4DF/D5/D6 internal rows.

No statutory XML generation, approval, financial posting,
database writes, commits or regulatory acceptance.

Only explicitly tagged internal evidence is accepted.
Existing unrelated row formats cannot be silently inferred.
"""

import json

from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import text

from app.services.payroll_regulatory_internal_reconciliation import (
    FORMS,
    InternalRegulatoryRow,
    InternalSnapshot,
    RegulatoryInternalError,
    build_internal_snapshot,
    verify_internal_snapshot,
)


INTERNAL_ROW_CODE = "ERP_INTERNAL_RECONCILIATION_V1"
INTERNAL_SCHEMA = "erp.internal-regulatory-row.v1"

DATABASE_KINDS = {
    "D1": "d1",
    "4DF": "4df",
    "D5": "d5",
    "D6": "d6",
}


class PersistedRegulatoryBridgeError(ValueError):
    pass


@dataclass(frozen=True)
class PersistedReportReference:
    form: str
    report_id: int


def _id(value, name):
    if type(value) is not int or value <= 0:
        raise PersistedRegulatoryBridgeError(
            f"Invalid {name}"
        )
    return value


def _parse_payload(raw):
    if not isinstance(raw, str):
        raise PersistedRegulatoryBridgeError(
            "Persisted payload must be JSON text"
        )

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        raise PersistedRegulatoryBridgeError(
            "Invalid persisted JSON payload"
        ) from None

    if not isinstance(payload, dict):
        raise PersistedRegulatoryBridgeError(
            "Persisted payload must be an object"
        )

    if payload.get("schema") != INTERNAL_SCHEMA:
        raise PersistedRegulatoryBridgeError(
            "Unverified internal row schema"
        )

    return payload


def row_from_persisted_record(
    record: Mapping,
    *,
    company_id: int,
    form: str,
    payroll_period: str,
):
    """Bind persisted row envelope to internal reconciliation data."""

    if form not in FORMS:
        raise PersistedRegulatoryBridgeError(
            "Unsupported form"
        )

    if record["company_id"] != company_id:
        raise PersistedRegulatoryBridgeError(
            "Cross-company report row"
        )

    if record["row_code"] != INTERNAL_ROW_CODE:
        raise PersistedRegulatoryBridgeError(
            "Non-internal report row cannot be promoted"
        )

    _id(record["id"], "row id")
    _id(record["row_no"], "row number")

    if (
        record["employee_id"] is None
        and record["employment_contract_id"] is None
    ):
        raise PersistedRegulatoryBridgeError(
            "Missing persisted employee/contract binding"
        )

    payload = _parse_payload(record["payload_json"])

    if payload.get("form") != form:
        raise PersistedRegulatoryBridgeError(
            "Stored form mismatch"
        )

    if payload.get("company_id") != company_id:
        raise PersistedRegulatoryBridgeError(
            "Stored company mismatch"
        )

    if payload.get("payroll_period") != payroll_period:
        raise PersistedRegulatoryBridgeError(
            "Stored reporting month mismatch"
        )

    # Both the persisted relational envelope and JSON evidence
    # must agree. Missing fields are not interpreted as matches.
    for key in (
        "employee_id",
        "employment_contract_id",
        "payroll_calculation_id",
    ):
        if (
            key not in payload
            or payload[key] != record[key]
        ):
            raise PersistedRegulatoryBridgeError(
                f"Persisted relational binding mismatch: {key}"
            )

    source = payload.get("source")

    if not isinstance(source, dict):
        raise PersistedRegulatoryBridgeError(
            "Missing explicit source provenance"
        )

    if set(source) != {
        "type", "id", "revision"
    }:
        raise PersistedRegulatoryBridgeError(
            "Incomplete source provenance"
        )

    source_id = _id(source["id"], "source id")
    source_revision = _id(
        source["revision"], "source revision"
    )

    source_type = source["type"]

    if (
        not isinstance(source_type, str)
        or not source_type.strip()
    ):
        raise PersistedRegulatoryBridgeError(
            "Missing source type"
        )

    # A payroll calculation source must match the relational
    # payroll calculation ID. HR sources are not automatically
    # classified as normatively verified by this reader.
    if source_type == "payroll_calculation":
        if record["payroll_calculation_id"] != source_id:
            raise PersistedRegulatoryBridgeError(
                "Payroll calculation source mismatch"
            )

    try:
        row = InternalRegulatoryRow(
            form=form,
            company_id=company_id,
            payroll_period=payroll_period,
            row_key=payload["row_key"],
            source_type=source_type,
            source_id=source_id,
            source_revision=source_revision,
            document_reference=payload["document_reference"],
            document_date=payload["document_date"],
            amounts=payload["amounts"],
            attributes=payload["attributes"],
            correction_of=payload.get("correction_of"),
        )
    except (KeyError, TypeError):
        raise PersistedRegulatoryBridgeError(
            "Incomplete internal row payload"
        ) from None

    return row


def assemble_persisted_internal_snapshot(
    *,
    company_id,
    payroll_period,
    version,
    report_rows,
    expected_source_totals,
    previous=None,
):
    """Use the existing canonical reconciliation engine."""

    _id(company_id, "company id")

    if set(report_rows) != FORMS:
        raise PersistedRegulatoryBridgeError(
            "Four explicit form collections required"
        )

    all_rows = []

    for form in sorted(FORMS):
        rows = report_rows[form]

        if not isinstance(rows, (list, tuple)):
            raise PersistedRegulatoryBridgeError(
                "Persisted rows must be ordered collections"
            )

        seen_numbers = set()

        for record in rows:
            row_no = record["row_no"]

            if row_no in seen_numbers:
                raise PersistedRegulatoryBridgeError(
                    "Repeated persisted row number"
                )

            seen_numbers.add(row_no)

            all_rows.append(
                row_from_persisted_record(
                    record,
                    company_id=company_id,
                    form=form,
                    payroll_period=payroll_period,
                )
            )

    try:
        snapshot = build_internal_snapshot(
            company_id=company_id,
            payroll_period=payroll_period,
            version=version,
            rows=all_rows,
            expected_source_totals=expected_source_totals,
            previous=previous,
        )

        verify_internal_snapshot(snapshot)

    except RegulatoryInternalError as exc:
        raise PersistedRegulatoryBridgeError(
            str(exc)
        ) from exc

    return snapshot


async def review_persisted_four_form_reports(
    db,
    *,
    company_id: int,
    payroll_period_id: int,
    payroll_period: str,
    report_references: Mapping[str, int],
    expected_source_totals: Mapping,
    version: int = 1,
    previous: InternalSnapshot | None = None,
):
    """Read four persisted draft reports and reconcile internal rows.

    expected_source_totals must originate from a separate verified
    source query. This service does not manufacture them from rows.

    Caller owns transaction and independent source verification.
    """

    _id(company_id, "company id")
    _id(payroll_period_id, "payroll period id")

    if set(report_references) != FORMS:
        raise PersistedRegulatoryBridgeError(
            "Exactly one report reference per form required"
        )

    ids = [
        _id(report_references[form], f"{form} report id")
        for form in sorted(FORMS)
    ]

    if len(set(ids)) != 4:
        raise PersistedRegulatoryBridgeError(
            "Report IDs must be distinct"
        )

    reports = {}

    # Read exact persisted report metadata; never create or
    # generate statutory reports here.
    for form in sorted(FORMS):
        report_id = report_references[form]

        result = await db.execute(
            text("""
                SELECT id, company_id, payroll_period_id,
                       report_kind, revision, status
                FROM payroll_regulatory_reports
                WHERE company_id = :company_id
                  AND id = :report_id
            """),
            {
                "company_id": company_id,
                "report_id": report_id,
            },
        )

        report = result.mappings().one_or_none()

        if report is None:
            raise PersistedRegulatoryBridgeError(
                f"{form}: report not found"
            )

        if (
            report["company_id"] != company_id
            or report["payroll_period_id"] != payroll_period_id
            or report["report_kind"] != DATABASE_KINDS[form]
            or report["status"] != "draft"
        ):
            raise PersistedRegulatoryBridgeError(
                f"{form}: incorrect report scope or status"
            )

        _id(report["revision"], "report revision")

        reports[form] = report

    collected = {}

    for form in sorted(FORMS):
        report = reports[form]

        result = await db.execute(
            text("""
                SELECT id, company_id,
                       payroll_regulatory_report_id,
                       row_no, row_code, payload_json,
                       employee_id, employment_contract_id,
                       payroll_calculation_id
                FROM payroll_regulatory_report_rows
                WHERE company_id = :company_id
                  AND payroll_regulatory_report_id = :report_id
                ORDER BY row_no, id
            """),
            {
                "company_id": company_id,
                "report_id": report["id"],
            },
        )

        rows = list(result.mappings().all())

        for row in rows:
            if (
                row["payroll_regulatory_report_id"]
                != report["id"]
            ):
                raise PersistedRegulatoryBridgeError(
                    f"{form}: report row binding mismatch"
                )

        collected[form] = rows

    return assemble_persisted_internal_snapshot(
        company_id=company_id,
        payroll_period=payroll_period,
        version=version,
        report_rows=collected,
        expected_source_totals=expected_source_totals,
        previous=previous,
    )
