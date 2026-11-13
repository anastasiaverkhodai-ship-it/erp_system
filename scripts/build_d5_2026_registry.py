"""Reproduce dated civil entries from the reviewed source chain.

This builder cannot approve its output: runtime trust is a separate pinned hash.
"""
from datetime import date, timedelta
import json
from pathlib import Path

from scripts.reconcile_d5_2026_classifier import ROOT, reconcile, identity

SOURCE_REFERENCE = 'DK003:2010 civil sections 1-9; Ministry consolidated XLS 2023 and amendments 12-17'
SOURCE_VERSION = 'civil-2026-a17-20261112'
AMENDMENT_17_FROM = date(2026, 4, 14)
OUTPUT = ROOT / 'app/data/d5_civil_classifier_2026.json'


def build_registry():
    review = reconcile()
    opening = review['opening_entries']
    updated = review['after_amendment_17_entries']
    before = {identity(r['profession_code'], r['professional_name']): r for r in opening}
    after = {identity(r['profession_code'], r['professional_name']): r for r in updated}
    entries = []
    for key, record in before.items():
        entries.append({
            **record,
            'effective_from': '2026-01-01',
            'effective_to': None if key in after else (AMENDMENT_17_FROM-timedelta(days=1)).isoformat(),
        })
    for key, record in after.items():
        if key not in before:
            entries.append({**record, 'effective_from': AMENDMENT_17_FROM.isoformat(), 'effective_to': None})
    entries.sort(key=lambda r: (r['profession_code'], r['professional_name'], r['effective_from']))
    return {'source_reference': SOURCE_REFERENCE, 'source_version': SOURCE_VERSION, 'entries': entries}


def registry_bytes():
    return (json.dumps(build_registry(), ensure_ascii=False, indent=2)+'\n').encode('utf-8')


if __name__ == '__main__':
    import hashlib
    raw = registry_bytes()
    OUTPUT.write_bytes(raw)
    print(f'Entries: {len(json.loads(raw)["entries"])}; SHA256: {hashlib.sha256(raw).hexdigest()}')
