"""Source-bound D5 text review without normative approval."""

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import re


class D5ContentReviewError(ValueError):
    pass


@dataclass(frozen=True)
class RuleCandidate:
    key: str
    search_terms: tuple[str, ...]


# These are evidence-search topics, not statutory columns,
# report values, or approved mappings.
RULE_TOPICS = (
    RuleCandidate(
        "hire_termination",
        ("укладено", "розірвано", "трудов"),
    ),
    RuleCandidate(
        "permanent_transfer",
        ("переведен", "посад"),
    ),
    RuleCandidate(
        "termination_legal_basis",
        ("причини звільнення", "закону"),
    ),
    RuleCandidate(
        "secondary_employment_change",
        ("основного місця роботи", "сумісництво"),
    ),
    RuleCandidate(
        "new_workplace_date",
        ("нового робочого місця", "дата"),
    ),
    RuleCandidate(
        "statutory_correction",
        ("уточнюючий", "додаток д5"),
    ),
)


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "div", "li", "tr", "br", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        if tag in ("p", "li", "tr", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract_text(name, content):
    suffix = Path(name).suffix.lower()

    if suffix not in (".html", ".htm", ".json", ".txt"):
        return None

    if len(content) > 25 * 1024 * 1024:
        raise D5ContentReviewError("Source size limit exceeded")

    try:
        raw = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise D5ContentReviewError(
            "Text encoding requires separate review"
        )

    if suffix in (".html", ".htm"):
        parser = VisibleHTML()
        parser.feed(raw)
        raw = "".join(parser.parts)

    if suffix == ".json":
        import json
        # Validate the JSON rather than accepting malformed text.
        json.loads(raw)

    return re.sub(r"\s+", " ", raw).strip()


def discover_review_evidence(name, content):
    if not isinstance(content, bytes):
        raise D5ContentReviewError(
            "Source bytes required"
        )

    digest = sha256(content).hexdigest()
    text = extract_text(name, content)

    if text is None:
        return {
            "path": name,
            "sha256": digest,
            "status": "FORMAT_REQUIRES_SEPARATE_REVIEW",
            "matches": [],
        }

    folded = text.casefold()
    matches = []

    for topic in RULE_TOPICS:
        # Require terms within one bounded text window.
        # This only locates candidates; it does not establish
        # that the paragraph means what the topic suggests.
        positions = [
            [m.start() for m in re.finditer(
                re.escape(term.casefold()), folded
            )]
            for term in topic.search_terms
        ]

        if any(not values for values in positions):
            continue

        found = None

        for offset in positions[0][:500]:
            left = max(0, offset - 180)
            right = min(len(text), offset + 900)
            window = folded[left:right]

            if all(
                term.casefold() in window
                for term in topic.search_terms
            ):
                found = (left, right)
                break

        if found is None:
            continue

        left, right = found
        excerpt = text[left:right]

        matches.append({
            "topic": topic.key,
            "source_offset": left,
            "excerpt": excerpt,
            "excerpt_sha256": sha256(
                excerpt.encode("utf-8")
            ).hexdigest(),
            "meaning_verified": False,
            "legal_authority_verified": False,
            "field_mapping_approved": False,
            "status": "REVIEW_REQUIRED",
        })

    return {
        "path": name,
        "sha256": digest,
        "status": "CONTENT_EXTRACTED_REVIEW_ONLY",
        "matches": matches,
    }


def build_content_inventory(root):
    root = Path(root).resolve(strict=True)

    if not root.is_dir():
        raise D5ContentReviewError(
            "Evidence root must be a directory"
        )

    results = []

    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise D5ContentReviewError(
                "Symlinked evidence entry prohibited"
            )

        if not path.is_file():
            continue

        if path.stat().st_size > 25 * 1024 * 1024:
            results.append({
                "path": str(path.relative_to(root)),
                "status": "SIZE_REQUIRES_SEPARATE_REVIEW",
                "matches": [],
            })
            continue

        results.append(
            discover_review_evidence(
                str(path.relative_to(root)),
                path.read_bytes(),
            )
        )

    topics = sorted({
        match["topic"]
        for result in results
        for match in result["matches"]
    })

    return {
        "source_file_count": len(results),
        "candidate_topic_count": len(topics),
        "candidate_topics": topics,
        "missing_topics": sorted(
            {item.key for item in RULE_TOPICS} - set(topics)
        ),
        "sources": results,
        "legal_source_approval": False,
        "official_field_mapping_approval": False,
        "official_rows_generated": 0,
        "official_generation": "BLOCKED",
        "status": "REVIEW_REQUIRED",
    }
