"""Review D5 field definitions, without statutory approval."""

import hashlib
import re

from app.services.payroll_d5_scoped_field_evidence import (
    D5SectionError,
    scoped_d5_section,
)


EXPECTED_FIELDS = frozenset(range(4, 19))

DEFINITION = re.compile(
    r"(?<!\w)граф(?:а|и)\s+"
    r"(\d{1,2})"
    r"(?:\s*,\s*(\d{1,2}))?"
    r"\s*[-–—]\s*",
    re.IGNORECASE,
)


def review_d5_field_definitions(
    source_name,
    source_sha256,
    source_text,
):
    if (
        not isinstance(source_name, str)
        or not source_name
    ):
        raise D5SectionError("Source name required")

    if not re.fullmatch(
        r"[0-9a-f]{64}",
        source_sha256 or "",
    ):
        raise D5SectionError("Source SHA256 required")

    section = scoped_d5_section(source_text)
    body = section["text"]

    anchors = list(DEFINITION.finditer(body))
    definitions = []

    for idx, match in enumerate(anchors):
        numbers = [
            int(value)
            for value in match.groups()
            if value is not None
        ]

        next_start = (
            anchors[idx + 1].start()
            if idx + 1 < len(anchors)
            else len(body)
        )

        definition = body[
            match.end():next_start
        ].strip()

        # Keep content bounded for the review register.
        excerpt = definition[:2000]

        for number in numbers:
            if number not in EXPECTED_FIELDS:
                continue

            definitions.append({
                "field_number": number,
                "source": source_name,
                "source_sha256": source_sha256,
                "absolute_offset": (
                    section["start_offset"]
                    + match.start()
                ),
                "shared_definition": len(numbers) > 1,
                "definition_excerpt": excerpt,
                "excerpt_sha256": hashlib.sha256(
                    excerpt.encode("utf-8")
                ).hexdigest(),
                "status": "REVIEW_REQUIRED",
                "meaning_approved": False,
                "allowed_values_approved": False,
                "legal_authority_approved": False,
            })

    counts = {
        number: sum(
            item["field_number"] == number
            for item in definitions
        )
        for number in EXPECTED_FIELDS
    }

    missing = sorted(
        n for n, count in counts.items()
        if count == 0
    )

    duplicates = sorted(
        n for n, count in counts.items()
        if count > 1
    )

    return {
        "source": source_name,
        "source_sha256": source_sha256,
        "section_start": section["start_offset"],
        "section_end": section["end_offset"],
        "expected_fields": sorted(EXPECTED_FIELDS),
        "defined_fields": sorted(
            n for n, count in counts.items()
            if count > 0
        ),
        "missing_fields": missing,
        "duplicate_fields": duplicates,
        "definitions": definitions,
        "technical_coverage": (
            "COMPLETE_CANDIDATE_SET"
            if not missing and not duplicates
            else "INCOMPLETE_OR_AMBIGUOUS"
        ),
        "legal_approval": False,
        "normative_mapping_approval": False,
        "official_rows_generated": 0,
        "official_generation": "BLOCKED",
    }


def review_extracted_manifest(manifest):
    candidates = [
        doc for doc in manifest.get("sources", [])
        if doc.get("source", "").endswith(
            "order243_buhgalter911_full.html"
        )
    ]

    if len(candidates) != 1:
        raise D5SectionError(
            "Unique source document required"
        )

    doc = candidates[0]

    if doc.get("status") != "EXTRACTED_REVIEW_ONLY":
        raise D5SectionError(
            "Source extraction incomplete"
        )

    text = "\n".join(
        block["text"]
        for block in doc.get("blocks", [])
        if isinstance(block.get("text"), str)
    )

    return review_d5_field_definitions(
        doc["source"],
        doc["source_sha256"],
        text,
    )
