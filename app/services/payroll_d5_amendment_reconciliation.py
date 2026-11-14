"""Review-only D5 profession amendment reconciliation.

No local file, hash, or synthetic source fixture establishes
official authenticity, complete amendment history, or
regulatory approval.
"""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import re

from app.services.payroll_d5_profession_effective_history import (
    D5ProfessionHistoryChange,
    review_d5_profession_history,
)


class D5AmendmentReconciliationError(ValueError):
    pass


EXPECTED_DATES = {
    1: date(2012, 9, 1),
    6: date(2017, 11, 1),
    7: date(2017, 11, 30),
}

VALID_COMPLETENESS = frozenset({
    "FULL_DOCUMENT_VERIFIED",
    "PARTIAL",
    "NOT_VERIFIED",
})

SHA = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class D5AmendmentEvidence:
    amendment_number: int
    effective_date: date
    source_reference: str
    source_sha256: str
    document_completeness: str
    profession_list_completeness: str


def _sha(value):
    return (
        type(value) is str
        and SHA.fullmatch(value) is not None
        and value != "0" * 64
    )


def _validate_evidence(item):
    if type(item) is not D5AmendmentEvidence:
        raise D5AmendmentReconciliationError(
            "Typed amendment evidence required"
        )

    if (
        type(item.amendment_number) is not int
        or item.amendment_number < 1
    ):
        raise D5AmendmentReconciliationError(
            "Invalid amendment number"
        )

    if type(item.effective_date) is not date:
        raise D5AmendmentReconciliationError(
            "Exact amendment effective date required"
        )

    if (
        type(item.source_reference) is not str
        or not item.source_reference.strip()
        or item.source_reference != item.source_reference.strip()
    ):
        raise D5AmendmentReconciliationError(
            "Valid source reference required"
        )

    if not _sha(item.source_sha256):
        raise D5AmendmentReconciliationError(
            "Valid source SHA256 required"
        )

    if (
        item.document_completeness not in VALID_COMPLETENESS
        or item.profession_list_completeness
        not in VALID_COMPLETENESS
    ):
        raise D5AmendmentReconciliationError(
            "Explicit source completeness required"
        )

    expected = EXPECTED_DATES.get(item.amendment_number)

    if expected is not None and item.effective_date != expected:
        raise D5AmendmentReconciliationError(
            "Confirmed amendment effective date mismatch"
        )

    return item


def reconcile_d5_amendments(
    *,
    profession_code,
    reporting_date,
    amendments,
    changes,
):
    """Reconcile candidate history with explicit amendment evidence.

    This proves internal consistency only. Completeness of all
    classifier amendments remains NOT_VERIFIED until independently
    established from the full normative source collection.
    """
    if type(amendments) not in (list, tuple):
        raise D5AmendmentReconciliationError(
            "Amendments collection required"
        )

    if type(changes) not in (list, tuple):
        raise D5AmendmentReconciliationError(
            "Profession changes collection required"
        )

    if len(amendments) > 10000 or len(changes) > 100000:
        raise D5AmendmentReconciliationError(
            "Reconciliation input limit exceeded"
        )

    sources = {}

    for item in amendments:
        _validate_evidence(item)

        if item.amendment_number in sources:
            raise D5AmendmentReconciliationError(
                "Duplicate amendment number"
            )

        sources[item.amendment_number] = item

    # Each (reference, digest, effective date) identifies
    # one candidate source, not statutory approval.
    by_source = {}

    for item in sources.values():
        identity = (
            item.source_reference,
            item.source_sha256,
            item.effective_date,
        )

        if identity in by_source:
            raise D5AmendmentReconciliationError(
                "Duplicate source identity"
            )

        by_source[identity] = item

    blockers = set()
    linked = []
    selected = []

    for item in changes:
        if type(item) is not D5ProfessionHistoryChange:
            raise D5AmendmentReconciliationError(
                "Typed profession change required"
            )

        identity = (
            item.source_reference,
            item.source_sha256,
            item.effective_date,
        )

        source = by_source.get(identity)

        if source is None:
            blockers.add("UNBOUND_PROFESSION_CHANGE")
            continue

        linked.append((source.amendment_number, item))

        if item.profession_code == profession_code:
            selected.append(item)

    history = review_d5_profession_history(
        profession_code=profession_code,
        reporting_date=reporting_date,
        changes=selected,
    )

    for number in EXPECTED_DATES:
        if number not in sources:
            blockers.add(f"AMENDMENT_{number}_MISSING")

    for item in sources.values():
        if item.document_completeness != "FULL_DOCUMENT_VERIFIED":
            blockers.add(
                f"AMENDMENT_{item.amendment_number}_DOCUMENT_INCOMPLETE"
            )

        if item.profession_list_completeness != (
            "FULL_DOCUMENT_VERIFIED"
        ):
            blockers.add(
                f"AMENDMENT_{item.amendment_number}_PROFESSIONS_INCOMPLETE"
            )

    # Amendment 7 is reported to restore 10 professions.
    # Count only unique, source-linked records. The actual
    # codes must come from the verified annex, not fixtures.
    restored_7 = [
        change.profession_code
        for number, change in linked
        if number == 7 and change.change_kind == "restored"
    ]

    if len(restored_7) != 10 or len(set(restored_7)) != 10:
        blockers.add("AMENDMENT_7_TEN_RESTORATIONS_NOT_VERIFIED")

    if history["candidate_state"] != "CANDIDATE_ACTIVE":
        blockers.add("PROFESSION_NOT_CANDIDATE_ACTIVE_ON_DATE")

    normalized = {
        "amendments": [
            {
                "number": item.amendment_number,
                "date": item.effective_date.isoformat(),
                "reference": item.source_reference,
                "sha256": item.source_sha256,
                "document_completeness": item.document_completeness,
                "profession_list_completeness": (
                    item.profession_list_completeness
                ),
            }
            for item in sorted(
                sources.values(),
                key=lambda entry: entry.amendment_number,
            )
        ],
        "linked_changes": sorted([
            {
                "amendment": number,
                "profession_code": item.profession_code,
                "kind": item.change_kind,
                "effective_date": item.effective_date.isoformat(),
            }
            for number, item in linked
        ], key=lambda entry: (
            entry["amendment"],
            entry["profession_code"],
            entry["kind"],
        )),
    }

    digest = sha256(
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    return {
        "profession_code": profession_code,
        "reporting_date": reporting_date.isoformat(),
        "candidate_history": history,
        "amendment_count": len(sources),
        "linked_profession_changes": len(linked),
        "unbound_profession_changes": len(changes) - len(linked),
        "amendment_7_restored_unique": len(set(restored_7)),
        "amendment_7_restored_entries": len(restored_7),
        "reconciliation_sha256": digest,
        "blockers": sorted(blockers),
        "internal_consistency": (
            "REVIEW_BLOCKED" if blockers else "CANDIDATE_CONSISTENT"
        ),
        "complete_classifier_history": "NOT_VERIFIED",
        "source_authenticity": "NOT_VERIFIED",
        "normative_approval": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }


async def review_persisted_d5_amendment_reconciliation(
    db,
    *,
    company_id,
    employment_event_id,
    amendments,
    changes,
):
    """Bind amendment reconciliation to real persisted HR source."""
    from app.services.payroll_d5_database_field_projection import (
        D5DatabaseProjectionError,
        project_persisted_d5_event,
    )

    persisted = await project_persisted_d5_event(
        db,
        company_id=company_id,
        employment_event_id=employment_event_id,
    )

    if persisted.get("source_binding") != (
        "PERSISTED_POSTGRESQL_RECORDS_VERIFIED"
    ):
        raise D5DatabaseProjectionError(
            "Verified persisted HR evidence required"
        )

    if persisted.get("official_generation") != "BLOCKED":
        raise D5DatabaseProjectionError(
            "Statutory generation must remain blocked"
        )

    candidate = persisted["candidate"]
    identity = candidate["identity"]

    if (
        identity.get("company_id") != company_id
        or identity.get("employment_event_id") != employment_event_id
    ):
        raise D5DatabaseProjectionError(
            "Persisted HR identity mismatch"
        )

    result = reconcile_d5_amendments(
        profession_code=candidate["fields"]["14"],
        reporting_date=date.fromisoformat(
            identity["effective_date"]
        ),
        amendments=amendments,
        changes=changes,
    )

    return {
        "company_id": company_id,
        "employment_event_id": employment_event_id,
        "persisted_erp_evidence_sha256": (
            persisted["persisted_erp_evidence_sha256"]
        ),
        "source_binding": persisted["source_binding"],
        "amendment_reconciliation": result,
        "normative_approval": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
