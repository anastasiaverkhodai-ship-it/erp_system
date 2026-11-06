"""Reproduce reference-data checks; passing does not grant statutory approval."""
from collections import Counter
from datetime import datetime, date
from pathlib import Path
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/normative_evidence"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def audit():
    current = EVIDENCE / "2026_11_06"
    for source in json.loads((current / "sources.json").read_text()):
        if hashlib.sha256((current / source["file"]).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError(f"Source bytes changed: {source['file']}")
    with zipfile.ZipFile(current / "classifier_ministry_working.xlsx") as archive:
        strings = ["".join(e.itertext()) for e in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        indices = []
        for sheet in (2, 3):
            entries = []
            for row in ET.fromstring(archive.read(f"xl/worksheets/sheet{sheet}.xml")).findall(".//m:row", NS)[1:]:
                cells = {}
                for cell in row:
                    value = cell.find("m:v", NS)
                    if value is not None:
                        text = strings[int(value.text)] if cell.get("t") == "s" else value.text
                        cells[re.sub(r"\d", "", cell.get("r"))] = " ".join(text.split())
                if not cells.get("A") or not cells.get("E"):
                    raise ValueError(f"Incomplete classifier row {row.get('r')}")
                if not re.fullmatch(r"[0-9]{4}(?:\.[0-9])?", cells["A"]):
                    raise ValueError(f"Invalid code: {cells['A']}")
                entries.append((cells["A"], cells["E"]))
            indices.append(Counter(entries))
    if indices[0] != indices[1] or any(n != 1 for n in indices[0].values()):
        raise ValueError("Appendices A/B disagree or contain duplicate pairs")

    # Audit the complete amendment copy without resolving its conflicting dates.
    word_ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(current / "amendment4_publisher.docx") as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    tables = [
        [[" ".join("".join(cell.itertext()).split())
          for cell in row.findall("w:tc", word_ns)]
         for row in table.findall("w:tr", word_ns)]
        for table in document.findall(".//w:tbl", word_ns)
    ]
    if len(tables) != 7:
        raise ValueError("Unexpected amendment 4 table inventory")
    for left, right in ((1, 4), (2, 5), (3, 6)):
        if Counter(tuple(row[1:]) for row in tables[left][1:]) != Counter(tuple(row[1:]) for row in tables[right][1:]):
            raise ValueError("Amendment 4 appendices disagree")

    raw = json.loads((EVIDENCE / "2026_11_05/annex_tables_review.json").read_text())
    decisions = json.loads((EVIDENCE / "2026_11_05/date_conflicts.json").read_text())
    ends = {(r["code"], r["start"], r["raw_end"]): r["resolved_end"] for r in decisions["issues"]}
    starts = {(r["code"], r["raw_start"]): r["resolved_start"] for r in decisions["resolved_start_dates"]}
    periods = {}
    for code, label, short, start, end in raw["tables"]["income_codes"]:
        end = ends.get((code, start, end), end)
        start = starts.get((code, start), start)
        first = datetime.strptime(start, "%d.%m.%Y").date()
        last = datetime.strptime(end, "%d.%m.%Y").date() if end else date.max
        if first > last:
            raise ValueError(f"Reversed dates for {code}")
        periods.setdefault(code, []).append((first, last))
    for code, spans in periods.items():
        spans.sort()
        if any(left[1] >= right[0] for left, right in zip(spans, spans[1:])):
            raise ValueError(f"Overlapping income validity for {code}")
    return {
        "classifier_unique_pairs_in_each_appendix": len(indices[0]),
        "classifier_appendices_equal": True,
        "amendment4_appendices_equal": True,
        "amendment4_cancellations": len(tables[1]) - 1,
        "amendment4_replacements": (len(tables[2]) - 1) // 2,
        "amendment4_additions": len(tables[3]) - 1,
        "amendment4_effective_date_status": "CONFLICT_UNRESOLVED",
        "income_rows": len(raw["tables"]["income_codes"]),
        "income_unique_codes": len(periods),
        "income_overlaps_after_documented_resolutions": 0,
        "production_approved": False,
        "scope": "Structural consistency and documented income-date conflicts only; not current classifier certification",
    }


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
