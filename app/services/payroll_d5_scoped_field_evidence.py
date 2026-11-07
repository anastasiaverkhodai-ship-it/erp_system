"""Fail-closed review of field evidence inside a D5-only section.

Does not approve statutory fields, source authenticity,
filing applicability, or official report generation.
"""

import hashlib
import re


class D5SectionError(ValueError):
    pass


# Section numbers refer to the source document's layout.
# They are not D5 form column numbers.
START = re.compile(
    r"(?<!\d)5\s*\.\s*"
    r"Додаток\s+Д\s*5\s+до\s+Розрахунку\s+ЮО\b",
    re.IGNORECASE,
)

END = re.compile(
    r"(?<!\d)6\s*\.\s*"
    r"Додаток\s+Д\s*6\s+до\s+Розрахунку\s+ЮО\b",
    re.IGNORECASE,
)

FIELD = re.compile(
    r"\bграф(?:а|и|і|у|ою|ах|ами)\s*"
    r"(?:№\s*)?(\d{1,2})\b",
    re.IGNORECASE,
)


def scoped_d5_section(source_text):
    if not isinstance(source_text, str):
        raise D5SectionError("Source text must be str")

    starts = list(START.finditer(source_text))

    if len(starts) != 1:
        raise D5SectionError(
            "Unique D5 section boundary not established"
        )

    start = starts[0].start()
    end_match = END.search(
        source_text, starts[0].end()
    )

    if end_match is None:
        raise D5SectionError(
            "D5 section end boundary not established"
        )

    end = end_match.start()

    if end <= starts[0].end():
        raise D5SectionError(
            "Invalid D5 section boundaries"
        )

    # Ensure another D5 heading cannot be silently included.
    if START.search(
        source_text,
        starts[0].end(),
        end,
    ):
        raise D5SectionError(
            "Duplicate D5 section inside boundaries"
        )

    return {
        "start_offset": start,
        "end_offset": end,
        "text": source_text[start:end],
    }


def evidence_from_document(source, source_sha256, text):
    if not isinstance(source, str) or not source:
        raise D5SectionError("Source identity required")

    if (
        not isinstance(source_sha256, str)
        or not re.fullmatch(
            r"[0-9a-f]{64}", source_sha256
        )
    ):
        raise D5SectionError(
            "Original source SHA256 required"
        )

    section = scoped_d5_section(text)
    content = section["text"]

    results = []

    for match in FIELD.finditer(content):
        left = max(0, match.start() - 180)
        right = min(
            len(content), match.end() + 380
        )

        excerpt = content[left:right].strip()

        results.append({
            "document": source,
            "source_sha256": source_sha256,
            "annex": "D5",
            "field_number_candidate": int(
                match.group(1)
            ),
            "absolute_offset": (
                section["start_offset"]
                + match.start()
            ),
            "excerpt": excerpt,
            "excerpt_sha256": hashlib.sha256(
                excerpt.encode("utf-8")
            ).hexdigest(),
            "field_meaning_verified": False,
            "legal_authority_verified": False,
            "mapping_approved": False,
            "status": "REVIEW_REQUIRED",
        })

    return {
        "document": source,
        "source_sha256": source_sha256,
        "annex": "D5",
        "section_start": section["start_offset"],
        "section_end": section["end_offset"],
        "section_sha256": hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest(),
        "field_candidate_count": len(results),
        "field_candidates": results,
        "normative_mapping": "NOT_APPROVED",
        "official_generation": "BLOCKED",
    }


def review_extracted_sources(extraction):
    records = []
    blockers = []

    if not isinstance(extraction, dict):
        raise D5SectionError(
            "Extraction manifest required"
        )

    for document in extraction.get("sources", []):
        name = document.get("source", "")

        # Only the original normative text candidate,
        # not copies of reviews or spreadsheet classifier.
        if not name.endswith(
            "order243_buhgalter911_full.html"
        ):
            continue

        if document.get("status") != (
            "EXTRACTED_REVIEW_ONLY"
        ):
            blockers.append(
                "ORDER243_SOURCE_NOT_EXTRACTED"
            )
            continue

        blocks = document.get("blocks", [])

        text = "\n".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block.get("text"), str)
        )

        try:
            records.append(
                evidence_from_document(
                    name,
                    document["source_sha256"],
                    text,
                )
            )

        except (D5SectionError, KeyError) as exc:
            blockers.append(
                "D5_SECTION_BOUNDARY_UNVERIFIED:"
                + str(exc)
            )

    if len(records) != 1:
        blockers.append(
            "UNIQUE_D5_NORMATIVE_SECTION_MISSING"
        )

    return {
        "scoped_documents": len(records),
        "field_candidates": sum(
            r["field_candidate_count"]
            for r in records
        ),
        "records": records,
        "blockers": blockers,
        "legal_approval": False,
        "field_mapping_approval": False,
        "official_rows_generated": 0,
        "official_generation": "BLOCKED",
        "status": "REVIEW_REQUIRED",
    }
