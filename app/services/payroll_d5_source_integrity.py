"""Fail-closed D5 classifier source integrity validation.

This module verifies technical source evidence only.
It never approves statutory sources, profession codes,
mappings or official report generation.
"""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
import re


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_HISTORICAL_SOURCES = frozenset({
    "base",
    "amendment_3",
    "amendment_4",
    "amendment_5",
})


class D5SourceIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class D5SourceArtifact:
    source_id: str
    relative_path: str
    sha256_digest: str
    effective_from: date
    effective_to: date | None = None
    independent_approval: bool = False


def verify_source_artifact(
    artifact: D5SourceArtifact,
    *,
    source_root: Path,
) -> dict:
    """Verify pinned bytes without granting legal approval."""
    if not isinstance(artifact, D5SourceArtifact):
        raise D5SourceIntegrityError("Source artifact required")

    if (
        not isinstance(artifact.source_id, str)
        or not artifact.source_id.strip()
    ):
        raise D5SourceIntegrityError("Source ID required")

    if (
        not isinstance(artifact.relative_path, str)
        or not artifact.relative_path.strip()
    ):
        raise D5SourceIntegrityError("Source path required")

    if not SHA256_PATTERN.fullmatch(
        artifact.sha256_digest or ""
    ):
        raise D5SourceIntegrityError("Invalid source digest")

    if not isinstance(artifact.effective_from, date):
        raise D5SourceIntegrityError("Effective date required")

    if artifact.effective_to is not None:
        if (
            not isinstance(artifact.effective_to, date)
            or artifact.effective_to < artifact.effective_from
        ):
            raise D5SourceIntegrityError(
                "Invalid effective-date interval"
            )

    if not isinstance(artifact.independent_approval, bool):
        raise D5SourceIntegrityError(
            "Invalid independent approval flag"
        )

    root = Path(source_root).resolve(strict=True)
    if not root.is_dir():
        raise D5SourceIntegrityError(
            "Source root must be a directory"
        )

    relative = Path(artifact.relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise D5SourceIntegrityError(
            "Unsafe source path"
        )

    candidate = root / relative

    if candidate.is_symlink():
        raise D5SourceIntegrityError(
            "Symlink source not allowed"
        )

    path = candidate.resolve(strict=True)

    if not path.is_relative_to(root) or not path.is_file():
        raise D5SourceIntegrityError(
            "Source outside approved root"
        )

    if any(parent.is_symlink() for parent in
           list(candidate.parents) if parent != root
           and parent.is_relative_to(root)):
        raise D5SourceIntegrityError(
            "Symlink directory not allowed"
        )

    digest = sha256()

    with path.open("rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)

    if digest.hexdigest() != artifact.sha256_digest:
        raise D5SourceIntegrityError(
            "Source SHA256 mismatch"
        )

    return {
        "source_id": artifact.source_id,
        "sha256_verified": True,
        "effective_from": artifact.effective_from.isoformat(),
        "effective_to": (
            artifact.effective_to.isoformat()
            if artifact.effective_to else None
        ),
        "independent_approval": artifact.independent_approval,
        "normative_content_verified": False,
        "official_generation_authorized": False,
    }


def verify_historical_bundle(
    artifacts,
    *,
    source_root: Path,
    required_sources=REQUIRED_HISTORICAL_SOURCES,
) -> dict:
    """Verify a complete technical bundle; never approve it."""
    artifacts = tuple(artifacts)

    ids = [item.source_id for item in artifacts]
    if len(ids) != len(set(ids)):
        raise D5SourceIntegrityError(
            "Duplicate source IDs"
        )

    required = set(required_sources)
    missing = required - set(ids)

    if missing:
        raise D5SourceIntegrityError(
            "Missing required sources: "
            + ", ".join(sorted(missing))
        )

    records = [
        verify_source_artifact(
            item,
            source_root=source_root,
        )
        for item in artifacts
    ]

    return {
        "technical_integrity": "PASS",
        "source_count": len(records),
        "required_source_count": len(required),
        "records": records,
        "normative_content_verified": False,
        "independent_normative_approval": False,
        "official_d5_generation": "BLOCKED",
    }
