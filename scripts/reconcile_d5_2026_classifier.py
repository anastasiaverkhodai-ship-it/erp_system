"""Reproduce the civil D5 classifier candidate from pinned primary evidence.

This audit deliberately cannot populate production approval registries.
Opening coverage is 2026-01-01, not the introduction date of each profession.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/normative_evidence/2026_11_12'


class ReconciliationError(ValueError):
    pass


def identity(code, name):
    """Comparison only: retain the original Appendix A spelling in output."""
    name = name.replace('’', "'").replace('ʼ', "'").replace('`', "'")
    name = re.sub(r'т\.\s*ін\.', 'т. ін.', name)
    name = re.sub(r'\s*([–—-])\s*', r'\1', name)
    return code, ' '.join(name.split()).casefold()


def index_entries(entries):
    result = {}
    for record in entries:
        code, name = record['profession_code'], record['professional_name']
        if re.fullmatch(r'[1-9][0-9]{3}(?:\.[0-9])?', code) is None or not name.strip():
            raise ReconciliationError('Invalid civil profession identity')
        key = identity(code, name)
        if key in result:
            raise ReconciliationError('Duplicate baseline identity')
        result[key] = dict(record)
    return result


def apply_amendment(entries, operations):
    """Apply atomically, including explicit many-to-one replacement rows.

    A shared target is allowed only when all its contributing operations are
    replacements of distinct existing source identities. Duplicate additions,
    missing old names and collisions with an untouched identity always fail.
    """
    before = index_entries(entries)
    removed, targets = set(), {}
    for event in operations:
        if not isinstance(event, (tuple, list)) or not event:
            raise ReconciliationError('Invalid operation')
        operation = event[0]
        expected = {'remove': 3, 'add': 3, 'replace': 5}
        if operation not in expected or len(event) != expected[operation]:
            raise ReconciliationError('Invalid operation shape')
        if operation in ('remove', 'replace'):
            old = identity(event[1], event[2])
            if old not in before or old in removed:
                raise ReconciliationError(f'Missing or repeated source identity: {event}')
            removed.add(old)
        if operation in ('add', 'replace'):
            code, name = event[-2:]
            # Reuse structural validation, including the civil scope boundary.
            index_entries([{'profession_code': code, 'professional_name': name}])
            target = identity(code, name)
            if target in targets:
                previous = targets[target]
                if operation != 'replace' or any(e[0] != 'replace' for e in previous):
                    raise ReconciliationError('Duplicate addition target')
                if (code, name) != tuple(previous[0][-2:]):
                    raise ReconciliationError('Ambiguous target spelling')
            targets.setdefault(target, []).append(event)
    after = {key: dict(value) for key, value in before.items() if key not in removed}
    for target, events in targets.items():
        if target in after:
            raise ReconciliationError('Target collides with an unchanged profession')
        code, name = events[0][-2:]
        after[target] = {'profession_code': code, 'professional_name': name}
    return sorted(after.values(), key=lambda r: (r['profession_code'], r['professional_name']))


def verify_incorporated(entries, operations):
    current = index_entries(entries)
    for event in operations:
        if event[0] in ('remove', 'replace') and identity(*event[1:3]) in current:
            raise ReconciliationError('Consolidated source retains an obsolete name')
        if event[0] in ('add', 'replace') and identity(*event[-2:]) not in current:
            raise ReconciliationError('Consolidated source lacks an introduced name')


def verify_appendices(document):
    if document['errors']:
        raise ReconciliationError('Unparsed source rows')
    a, b = map(lambda rows: Counter(tuple(e) for e in rows), (document['A'], document['B']))
    expected_a, expected_b = map(lambda rows: Counter(tuple(e) for e in rows),
                               (document['accepted_a_only'], document['accepted_b_only']))
    if a-b != expected_a or b-a != expected_b:
        raise ReconciliationError('Unreviewed Appendix A/B discrepancy')
    return document['A']


def load_checked(path, digest):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ReconciliationError(f'Evidence hash mismatch: {path.name}')
    return raw


def reconcile():
    manifest = json.loads((EVIDENCE / 'reconciliation_inputs.json').read_text())
    for item in manifest['files']:
        load_checked(ROOT / item['path'], item['sha256'])
    baseline = json.loads((ROOT / manifest['baseline']).read_text())['entries']
    events = {}
    for number in (11, 12, 13, 14, 16, 17):
        document = json.loads((EVIDENCE / f'amendment_{number}_operations.json').read_text())
        events[number] = verify_appendices(document)
    verify_incorporated(baseline, events[11])
    counts = {'consolidated_2023': len(baseline)}
    current = baseline
    for number in (12, 13, 14, 16, 17):
        current = apply_amendment(current, events[number])
        counts[str(number)] = len(current)
        if number == 16:
            opening = current
    return {
        'supported_from': '2026-01-01',
        'scope': 'civil sections 1-9; military section 0 is not supported',
        'counts': counts,
        'opening_entries': opening,
        'after_amendment_17_entries': current,
        'normative_approved': False,
        'official_generation_authorized': False,
        'remaining_approval_requirement': 'Confirm amendment 17 commencement from applicable primary legal evidence; do not infer it from publication or signature alone.',
    }


if __name__ == '__main__':
    result = reconcile()
    target = EVIDENCE / 'reconciled_civil_classifier_candidate.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'counts': result['counts'], 'normative_approved': False}))
