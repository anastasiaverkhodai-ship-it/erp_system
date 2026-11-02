"""Opt-in audit of actual source files.

No network access is performed automatically.
Pass --source-dir to inspect a local directory of official downloads.
Does not approve sources, fields or statutory generation.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path

MAX_BYTES = 40 * 1024 * 1024


def audit(root: Path):
    base = root.resolve(strict=True)
    if not base.is_dir():
        raise ValueError("Source directory required")

    records = []

    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink forbidden: {path}")

        if not path.is_file():
            continue

        if not path.resolve(strict=True).is_relative_to(base):
            raise ValueError("Source path escapes audit root")

        if path.suffix.lower() not in {".pdf", ".html", ".htm", ".xml"}:
            continue

        size = path.stat().st_size
        if not 0 < size <= MAX_BYTES:
            raise ValueError(f"Invalid artifact size: {path}")

        digest = sha256(path.read_bytes()).hexdigest()

        records.append({
            "relative_path": str(path.relative_to(base)),
            "byte_length": size,
            "sha256": digest,
            "publisher_authenticity": "NOT_VERIFIED",
            "legal_revision": "NOT_VERIFIED",
            "effective_dates": "NOT_VERIFIED",
            "statutory_mapping_approval": False,
        })

    return {
        "artifact_count": len(records),
        "artifacts": records,
        "official_generation": "BLOCKED",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(
        audit(Path(args.source_dir)),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
