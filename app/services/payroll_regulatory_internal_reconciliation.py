"""Internal, source-bound reconciliation of D1/4DF/D5/D6 data.

This module intentionally does NOT:
- generate statutory XML;
- approve normative mappings or classifiers;
- authorize official generated status;
- mark reports accepted by a regulator;
- replace independent payroll source verification.

Its immutable payloads are internal preparation evidence only.
"""

from __future__ import annotations

import hashlib
import json
import re

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence


FORMS = frozenset(("D1", "4DF", "D5", "D6"))
PERIOD_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")

# This is an internal classification only.
# It is NOT an official form-field registry.
FINANCIAL_FORMS = frozenset(("D1", "4DF"))


class RegulatoryInternalError(ValueError):
    pass


def _integer(value, name):
    if type(value) is not int or value <= 0:
        raise RegulatoryInternalError(
            f"{name} must be a positive integer"
        )
    return value


def _string(value, name, *, max_length=240):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > max_length
    ):
        raise RegulatoryInternalError(
            f"{name} must be a nonempty bounded string"
        )
    return value.strip()


def _period(value):
    if (
        not isinstance(value, str)
        or not PERIOD_RE.fullmatch(value)
    ):
        raise RegulatoryInternalError(
            "Period must be YYYY-MM"
        )
    return value


def _decimal(value, name):
    if isinstance(value, bool) or isinstance(value, float):
        raise RegulatoryInternalError(
            f"{name}: floating point is not accepted"
        )

    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise RegulatoryInternalError(
            f"{name}: invalid decimal"
        ) from None

    if not number.is_finite():
        raise RegulatoryInternalError(
            f"{name}: non-finite decimal"
        )

    if number.as_tuple().exponent < -2:
        raise RegulatoryInternalError(
            f"{name}: more than two decimal places"
        )

    if number.copy_abs() > Decimal("99999999999999.99"):
        raise RegulatoryInternalError(
            f"{name}: out of range"
        )

    return number.quantize(Decimal("0.01"))


def _date(value, name):
    if not isinstance(value, str):
        raise RegulatoryInternalError(
            f"{name}: ISO date required"
        )

    try:
        normalized = date.fromisoformat(value).isoformat()
    except ValueError:
        raise RegulatoryInternalError(
            f"{name}: invalid ISO date"
        ) from None

    if value != normalized:
        raise RegulatoryInternalError(
            f"{name}: noncanonical date"
        )

    return value


def _json_text(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True)
class InternalRegulatoryRow:
    form: str
    company_id: int
    payroll_period: str
    row_key: str

    source_type: str
    source_id: int
    source_revision: int

    document_reference: str
    document_date: str

    # Internal financial components, NOT official XML fields.
    amounts: Mapping[str, object]

    # Candidate attributes and classifications.
    # No attribute is approved as a statutory code here.
    attributes: Mapping[str, str]

    # Earlier internal row key being corrected, if applicable.
    correction_of: str | None = None


def normalize_row(row: InternalRegulatoryRow) -> dict:
    if not isinstance(row, InternalRegulatoryRow):
        raise RegulatoryInternalError(
            "InternalRegulatoryRow required"
        )

    if row.form not in FORMS:
        raise RegulatoryInternalError("Unknown form")

    company_id = _integer(row.company_id, "company_id")
    period = _period(row.payroll_period)

    row_key = _string(row.row_key, "row_key")
    source_type = _string(row.source_type, "source_type")

    source_id = _integer(row.source_id, "source_id")
    source_revision = _integer(
        row.source_revision, "source_revision"
    )

    document_reference = _string(
        row.document_reference,
        "document_reference",
    )

    document_date = _date(
        row.document_date,
        "document_date",
    )

    if (
        not isinstance(row.amounts, Mapping)
        or not isinstance(row.attributes, Mapping)
    ):
        raise RegulatoryInternalError(
            "Mappings required"
        )

    if row.form in FINANCIAL_FORMS and not row.amounts:
        raise RegulatoryInternalError(
            "Financial row requires amounts"
        )

    amounts = {}

    for raw_key, raw_value in row.amounts.items():
        key = _string(raw_key, "amount key", max_length=100)

        if key in amounts:
            raise RegulatoryInternalError(
                "Duplicate normalized amount key"
            )

        amounts[key] = str(
            _decimal(raw_value, key)
        )

    attributes = {}

    for raw_key, raw_value in row.attributes.items():
        key = _string(
            raw_key, "attribute key", max_length=100
        )

        if key in attributes:
            raise RegulatoryInternalError(
                "Duplicate normalized attribute key"
            )

        attributes[key] = _string(
            raw_value,
            f"attribute {key}",
            max_length=500,
        )

    correction_of = None

    if row.correction_of is not None:
        correction_of = _string(
            row.correction_of, "correction_of"
        )

        if correction_of == row_key:
            raise RegulatoryInternalError(
                "Self-correction forbidden"
            )

    return {
        "form": row.form,
        "company_id": company_id,
        "payroll_period": period,
        "row_key": row_key,
        "source_type": source_type,
        "source_id": source_id,
        "source_revision": source_revision,
        "document_reference": document_reference,
        "document_date": document_date,
        "amounts": dict(sorted(amounts.items())),
        "attributes": dict(sorted(attributes.items())),
        "correction_of": correction_of,
    }


@dataclass(frozen=True)
class InternalSnapshot:
    company_id: int
    payroll_period: str
    version: int

    # Canonical JSON is immutable text rather than a mutable dict.
    canonical_json: str
    sha256: str

    official_generation_authorized: bool = False
    statutory_mapping_verified: bool = False
    regulator_accepted: bool = False


def build_internal_snapshot(
    *,
    company_id: int,
    payroll_period: str,
    version: int,
    rows: Sequence[InternalRegulatoryRow],
    expected_source_totals: Mapping[str, Mapping[str, object]],
    previous: InternalSnapshot | None = None,
) -> InternalSnapshot:
    """Freeze internal evidence after exact source-total reconciliation.

    The source totals must come independently from verified payroll
    records; passing self-computed totals is not independent proof.
    """

    company_id = _integer(company_id, "company_id")
    period = _period(payroll_period)
    version = _integer(version, "version")

    if isinstance(rows, (str, bytes)) or not isinstance(
        rows, Sequence
    ):
        raise RegulatoryInternalError(
            "Rows must be a sequence"
        )

    if not isinstance(expected_source_totals, Mapping):
        raise RegulatoryInternalError(
            "Independent expected totals required"
        )

    if previous is None:
        if version != 1:
            raise RegulatoryInternalError(
                "Initial snapshot version must be 1"
            )
    else:
        if (
            previous.company_id != company_id
            or previous.payroll_period != period
            or version != previous.version + 1
        ):
            raise RegulatoryInternalError(
                "Invalid snapshot version chain"
            )

        if hashlib.sha256(
            previous.canonical_json.encode("utf-8")
        ).hexdigest() != previous.sha256:
            raise RegulatoryInternalError(
                "Previous snapshot integrity failed"
            )

    normalized = [normalize_row(row) for row in rows]

    seen = set()
    index = {}

    for item in normalized:
        if (
            item["company_id"] != company_id
            or item["payroll_period"] != period
        ):
            raise RegulatoryInternalError(
                "Cross-company or cross-period source"
            )

        key = (item["form"], item["row_key"])

        if key in seen:
            raise RegulatoryInternalError(
                "Duplicate internal row key"
            )

        seen.add(key)
        index[key] = item

    # Only explicitly chained internal corrections are allowed.
    for item in normalized:
        parent = item["correction_of"]

        if parent is None:
            continue

        parent_key = (item["form"], parent)

        if parent_key not in index:
            raise RegulatoryInternalError(
                "Missing correction predecessor"
            )

        earlier = index[parent_key]

        if (
            item["source_type"] != earlier["source_type"]
            or item["source_id"] != earlier["source_id"]
            or item["source_revision"]
                <= earlier["source_revision"]
        ):
            raise RegulatoryInternalError(
                "Correction source lineage mismatch"
            )

    # Rejected cycles, including multi-step cycles.
    for item in normalized:
        cursor = item
        visited = set()

        while cursor["correction_of"] is not None:
            key = (cursor["form"], cursor["row_key"])

            if key in visited:
                raise RegulatoryInternalError(
                    "Cyclic correction lineage"
                )

            visited.add(key)
            cursor = index[
                (cursor["form"], cursor["correction_of"])
            ]

    totals = {form: {} for form in sorted(FORMS)}

    # Only terminal revisions contribute to current totals.
    superseded = {
        (item["form"], item["correction_of"])
        for item in normalized
        if item["correction_of"] is not None
    }

    active_rows = [
        item for item in normalized
        if (item["form"], item["row_key"]) not in superseded
    ]

    for item in active_rows:
        bucket = totals[item["form"]]

        for key, value in item["amounts"].items():
            bucket[key] = (
                bucket.get(key, Decimal("0.00"))
                + Decimal(value)
            )

    normalized_expected = {}

    if set(expected_source_totals) != set(FORMS):
        raise RegulatoryInternalError(
            "Independent totals required for all four forms"
        )

    for form in sorted(FORMS):
        supplied = expected_source_totals[form]

        if not isinstance(supplied, Mapping):
            raise RegulatoryInternalError(
                "Expected totals must be mappings"
            )

        expected = {
            _string(key, "expected amount key", max_length=100):
            _decimal(value, "expected amount")
            for key, value in supplied.items()
        }

        actual = totals[form]

        if set(expected) != set(actual):
            raise RegulatoryInternalError(
                f"{form}: source-total components differ"
            )

        for key in expected:
            if actual[key] != expected[key]:
                raise RegulatoryInternalError(
                    f"{form}: source mismatch for {key}"
                )

        normalized_expected[form] = {
            key: str(expected[key])
            for key in sorted(expected)
        }

    # These are internal evidence assertions, not regulatory approval.
    payload = {
        "schema": "erp.internal-regulatory-reconciliation.v1",
        "company_id": company_id,
        "payroll_period": period,
        "version": version,
        "previous_sha256": (
            previous.sha256 if previous else None
        ),
        "rows": sorted(
            normalized,
            key=lambda item: (
                item["form"],
                item["row_key"],
            ),
        ),
        "active_row_keys": sorted(
            (item["form"], item["row_key"])
            for item in active_rows
        ),
        "independent_source_totals": normalized_expected,
        "statutory_mapping_verified": False,
        "official_generation_authorized": False,
        "regulator_accepted": False,
    }

    canonical = _json_text(payload)
    digest = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()

    return InternalSnapshot(
        company_id=company_id,
        payroll_period=period,
        version=version,
        canonical_json=canonical,
        sha256=digest,
    )


def verify_internal_snapshot(snapshot: InternalSnapshot) -> dict:
    if not isinstance(snapshot, InternalSnapshot):
        raise RegulatoryInternalError(
            "Internal snapshot required"
        )

    digest = hashlib.sha256(
        snapshot.canonical_json.encode("utf-8")
    ).hexdigest()

    if digest != snapshot.sha256:
        raise RegulatoryInternalError(
            "Snapshot digest mismatch"
        )

    payload = json.loads(snapshot.canonical_json)

    if _json_text(payload) != snapshot.canonical_json:
        raise RegulatoryInternalError(
            "Noncanonical snapshot"
        )

    if (
        payload["company_id"] != snapshot.company_id
        or payload["payroll_period"] != snapshot.payroll_period
        or payload["version"] != snapshot.version
    ):
        raise RegulatoryInternalError(
            "Snapshot metadata mismatch"
        )

    for flag in (
        "statutory_mapping_verified",
        "official_generation_authorized",
        "regulator_accepted",
    ):
        if (
            payload.get(flag) is not False
            or getattr(snapshot, flag) is not False
        ):
            raise RegulatoryInternalError(
                "Internal snapshot cannot authorize " + flag
            )

    return payload
