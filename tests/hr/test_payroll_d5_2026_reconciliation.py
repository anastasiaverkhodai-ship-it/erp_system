"""Whole civil classifier reconciliation and atomic amendment safeguards."""
from copy import deepcopy

import pytest
from scripts.reconcile_d5_2026_classifier import (
    ReconciliationError, apply_amendment, reconcile, verify_incorporated,
)


def entry(name, code='1229.3'):
    return {'profession_code': code, 'professional_name': name}


def test_complete_pinned_primary_chain():
    result = reconcile()
    assert result['counts'] == {
        'consolidated_2023': 9119, '12': 9138, '13': 9171,
        '14': 9180, '16': 9145, '17': 9151,
    }
    assert result['normative_approved'] is False
    assert result['official_generation_authorized'] is False
    before = {(e['profession_code'], e['professional_name']) for e in result['opening_entries']}
    after = {(e['profession_code'], e['professional_name']) for e in result['after_amendment_17_entries']}
    assert ('1229.3', 'Керівник апарату') in before
    assert ('1229.3', 'Керуючий справами виконавчого апарату') in before
    assert ('1229.3', 'Керівник апарату') not in after
    assert ('1229.3', 'Керуючий справами виконавчого апарату') not in after
    assert ('1229.3', 'Керуючий (секретар) виконавчого комітету (апарату)') in after


def test_many_to_one_rename_is_atomic_and_preserves_other_professions():
    before = [entry('A'), entry('B'), entry('Unchanged')]
    original = deepcopy(before)
    result = apply_amendment(before, [
        ['replace', '1229.3', 'A', '1229.3', 'C'],
        ['replace', '1229.3', 'B', '1229.3', 'C'],
    ])
    assert before == original
    assert result == [entry('C'), entry('Unchanged')]


@pytest.mark.parametrize('events', [
    [['remove', '1229.3', 'Missing']],
    [['remove', '1229.3', 'A'], ['remove', '1229.3', 'A']],
    [['add', '1229.3', 'B'], ['add', '1229.3', 'B']],
    [['replace', '1229.3', 'A', '1229.3', 'B'], ['add', '1229.3', 'B']],
    [['replace', '1229.3', 'A', '1229.3', 'Unchanged']],
    [['add', '0123', 'Military']],
    [['unknown', '1229.3', 'A']],
])
def test_invalid_batch_does_not_mutate_input(events):
    before = [entry('A'), entry('Unchanged')]
    original = deepcopy(before)
    with pytest.raises(ReconciliationError):
        apply_amendment(before, events)
    assert before == original


def test_incorporation_requires_both_removal_and_addition():
    event = [['replace', '1229.3', 'Old', '1229.3', 'New']]
    verify_incorporated([entry('New')], event)
    for invalid in ([entry('Old'), entry('New')], [entry('Unrelated')]):
        with pytest.raises(ReconciliationError):
            verify_incorporated(invalid, event)


def test_pinned_operations_are_reproducible_from_pdf_tables():
    import json
    from scripts.extract_d5_reviewed_operations import extract_operations
    from scripts.reconcile_d5_2026_classifier import EVIDENCE
    for number in (11, 12, 13, 14, 16, 17):
        actual = extract_operations(number, EVIDENCE)
        pinned = json.loads((EVIDENCE / f'amendment_{number}_operations.json').read_text())
        assert actual['errors'] == []
        assert [list(e) for e in actual['A']] == pinned['A']
        assert [list(e) for e in actual['B']] == pinned['B']


def test_pinned_evidence_tampering_is_rejected(tmp_path):
    import hashlib
    from scripts.reconcile_d5_2026_classifier import load_checked
    path = tmp_path / 'evidence.json'
    original = b'{"code": "1234"}'
    path.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    assert load_checked(path, digest) == original
    path.write_bytes(original + b' ')
    with pytest.raises(ReconciliationError, match='hash mismatch'):
        load_checked(path, digest)
