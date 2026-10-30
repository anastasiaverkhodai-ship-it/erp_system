"""Integrity validation for pre-mapped statutory report rows.

Does not create statutory codes or authorize source mappings.
A separate trusted mapping approval remains mandatory.
"""
import hashlib
import json


class RegulatoryGeneratedIntegrityError(ValueError):
    pass


KINDS = {
    "d1": "D1",
    "4df": "4DF",
    "d5": "D5",
    "d6": "D6",
}


def validate_mapped_report_rows(
    *,
    report_kind,
    company_id,
    period_id,
    rows,
    approved_mapping_ids,
):
    if report_kind not in KINDS:
        raise RegulatoryGeneratedIntegrityError(
            "Unsupported report kind"
        )

    if not rows:
        raise RegulatoryGeneratedIntegrityError(
            "No statutory rows available"
        )

    if not approved_mapping_ids:
        raise RegulatoryGeneratedIntegrityError(
            "No independently approved normative mapping"
        )

    expected_prefix = KINDS[report_kind] + "_"
    seen = set()
    slices_by_source = {}
    previous_number = 0
    fingerprints = []

    for row in rows:
        if row.company_id != company_id:
            raise RegulatoryGeneratedIntegrityError(
                "Cross-company report row"
            )

        if row.row_no != previous_number + 1:
            raise RegulatoryGeneratedIntegrityError(
                "Non-contiguous report row numbering"
            )
        previous_number = row.row_no

        if (
            not row.row_code.startswith(expected_prefix)
            or row.row_code == "SOURCE_EVIDENCE_V1"
            or row.row_code.endswith("_EVIDENCE_V1")
            or row.row_code.endswith("_REVIEWED_SOURCE_V1")
        ):
            raise RegulatoryGeneratedIntegrityError(
                "Internal source evidence is not a statutory row"
            )

        try:
            payload = json.loads(row.payload_json)
        except (ValueError, TypeError) as exc:
            raise RegulatoryGeneratedIntegrityError(
                "Invalid statutory row payload"
            ) from exc

        if not isinstance(payload, dict):
            raise RegulatoryGeneratedIntegrityError(
                "Statutory row must be a JSON object"
            )

        if (
            payload.get("company_id") != company_id
            or payload.get("payroll_period_id") != period_id
            or payload.get("report_kind") != report_kind
        ):
            raise RegulatoryGeneratedIntegrityError(
                "Report source identity mismatch"
            )

        mapping_id = payload.get("normative_mapping_id")
        if (
            type(mapping_id) is not int
            or mapping_id not in approved_mapping_ids
        ):
            raise RegulatoryGeneratedIntegrityError(
                "Unapproved normative mapping"
            )

        source_id = payload.get("source_id")
        if type(source_id) is not int or source_id <= 0:
            raise RegulatoryGeneratedIntegrityError(
                "Verified source identifier required"
            )

        slice_number = payload.get("slice_number")
        slice_count = payload.get("slice_count")

        if slice_number is None and slice_count is None:
            key = (row.row_code, source_id)
            if key in seen:
                raise RegulatoryGeneratedIntegrityError(
                    "Duplicate statutory row source"
                )
        else:
            if (
                report_kind != "4df"
                or type(slice_number) is not int
                or type(slice_count) is not int
                or slice_count < 1
                or not 1 <= slice_number <= slice_count
            ):
                raise RegulatoryGeneratedIntegrityError(
                    "Invalid dated statutory slice"
                )

            key = (row.row_code, source_id, slice_number)

            group_key = (row.row_code, source_id)
            group = slices_by_source.setdefault(
                group_key,
                {"count": slice_count, "numbers": set()},
            )

            if group["count"] != slice_count:
                raise RegulatoryGeneratedIntegrityError(
                    "Inconsistent dated slice count"
                )

            group["numbers"].add(slice_number)

        if key in seen:
            raise RegulatoryGeneratedIntegrityError(
                "Duplicate statutory row source"
            )

        seen.add(key)

        source_fingerprint = payload.get("source_sha256")
        if (
            not isinstance(source_fingerprint, str)
            or len(source_fingerprint) != 64
            or any(c not in "0123456789abcdef"
                   for c in source_fingerprint)
        ):
            raise RegulatoryGeneratedIntegrityError(
                "Source fingerprint required"
            )

        fingerprints.append(
            hashlib.sha256(
                row.payload_json.encode("utf-8")
            ).hexdigest()
        )

    for group in slices_by_source.values():
        if group["numbers"] != set(range(1, group["count"] + 1)):
            raise RegulatoryGeneratedIntegrityError(
                "Incomplete dated statutory slice sequence"
            )

    return tuple(fingerprints)
