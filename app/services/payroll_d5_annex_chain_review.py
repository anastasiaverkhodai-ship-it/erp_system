"""Deterministic, review-only D5 multi-annex source-chain control.

This validates candidate chronology and internal consistency only.
It never proves official document authenticity, authorizes normative
mappings, writes to the database, or generates statutory rows.
"""

from datetime import date
from hashlib import sha256
import json

from app.services.payroll_d5_verified_annex_ingestion import (
    D5AnnexIngestionError,
    review_d5_annex_manifest,
)


class D5AnnexChainError(ValueError):
    pass


def _digest(value):
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def review_d5_annex_chain(
    *,
    source_root,
    manifest_paths,
):
    """Validate multiple candidate amendment annexes as one chain.

    Numbering gaps are exposed, never silently filled.
    Earlier source states cannot be inferred from partial evidence.
    """
    if type(manifest_paths) not in (list, tuple):
        raise D5AnnexChainError(
            "Manifest collection must be a list or tuple"
        )

    if not 1 <= len(manifest_paths) <= 128:
        raise D5AnnexChainError(
            "Invalid manifest collection size"
        )

    if any(
        type(path) is not str or not path
        for path in manifest_paths
    ):
        raise D5AnnexChainError(
            "Invalid manifest path"
        )

    if len(manifest_paths) != len(set(manifest_paths)):
        raise D5AnnexChainError(
            "Duplicate annex manifest path"
        )

    documents = []
    events = []

    for manifest_path in manifest_paths:
        try:
            reviewed = review_d5_annex_manifest(
                source_root=source_root,
                manifest_path=manifest_path,
            )
        except (D5AnnexIngestionError, OSError) as exc:
            raise D5AnnexChainError(
                "Annex source validation failed"
            ) from exc

        amendment = reviewed["amendment"]
        changes = reviewed["changes"]

        if type(amendment.amendment_number) is not int:
            raise D5AnnexChainError(
                "Invalid amendment number"
            )

        if type(amendment.effective_date) is not date:
            raise D5AnnexChainError(
                "Invalid amendment effective date"
            )

        documents.append({
            "number": amendment.amendment_number,
            "effective_date": amendment.effective_date.isoformat(),
            "source_reference": amendment.source_reference,
            "source_sha256": amendment.source_sha256,
            "annex_sha256": reviewed["annex_sha256"],
            "row_count": reviewed["candidate_rows"],
            "document_completeness": amendment.document_completeness,
            "profession_list_completeness": (
                amendment.profession_list_completeness
            ),
        })

        for change in changes:
            if change.effective_date != amendment.effective_date:
                raise D5AnnexChainError(
                    "Event and annex dates differ"
                )

            events.append({
                "amendment_number": amendment.amendment_number,
                "effective_date": change.effective_date.isoformat(),
                "profession_code": change.profession_code,
                "change_kind": change.change_kind,
                "source_reference": change.source_reference,
                "source_sha256": change.source_sha256,
            })

    documents.sort(key=lambda item: item["number"])

    numbers = [item["number"] for item in documents]

    if len(numbers) != len(set(numbers)):
        raise D5AnnexChainError(
            "Duplicate amendment number"
        )

    for previous, current in zip(documents, documents[1:]):
        if previous["effective_date"] >= current["effective_date"]:
            raise D5AnnexChainError(
                "Amendment chronology is inconsistent"
            )

    seen_events = set()

    for event in events:
        identity = (
            event["profession_code"],
            event["effective_date"],
        )

        if identity in seen_events:
            raise D5AnnexChainError(
                "Conflicting same-date profession amendments"
            )

        seen_events.add(identity)

    events.sort(key=lambda item: (
        item["effective_date"],
        item["amendment_number"],
        item["profession_code"],
    ))

    missing_numbers = tuple(
        number
        for number in range(1, max(numbers) + 1)
        if number not in set(numbers)
    )

    partial_documents = tuple(
        item["number"]
        for item in documents
        if (
            item["document_completeness"]
            != "FULL_DOCUMENT_VERIFIED"
            or item["profession_list_completeness"]
            != "FULL_DOCUMENT_VERIFIED"
        )
    )

    evidence_payload = {
        "documents": documents,
        "events": events,
        "missing_amendment_numbers": list(missing_numbers),
        "partial_document_numbers": list(partial_documents),
    }

    blockers = [
        "D5_NORMATIVE_SOURCE_CHAIN_NOT_APPROVED",
        "D5_INDEPENDENT_AUTHENTICITY_NOT_VERIFIED",
        "D5_FULL_CLASSIFIER_HISTORY_NOT_VERIFIED",
    ]

    if missing_numbers:
        blockers.append(
            "D5_AMENDMENT_NUMBER_COVERAGE_INCOMPLETE"
        )

    if partial_documents:
        blockers.append(
            "D5_ANNEX_CONTENT_COVERAGE_INCOMPLETE"
        )

    return {
        "schema": "D5_ANNEX_CHAIN_REVIEW_V1",
        "amendment_count": len(documents),
        "candidate_event_count": len(events),
        "amendment_numbers": tuple(numbers),
        "missing_amendment_numbers": missing_numbers,
        "partial_document_numbers": partial_documents,
        "documents": tuple(documents),
        "events": tuple(events),
        "chain_sha256": _digest(evidence_payload),
        "source_integrity": "LOCAL_BYTES_VERIFIED",
        "independent_authenticity": "NOT_VERIFIED",
        "full_classifier_history": "NOT_VERIFIED",
        "normative_approval": "NOT_APPROVED",
        "review_only": True,
        "blockers": tuple(blockers),
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
