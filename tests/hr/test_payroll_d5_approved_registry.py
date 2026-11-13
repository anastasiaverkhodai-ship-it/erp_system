from datetime import date
import json

import pytest
from app.services.payroll_d5_approved_registry import (
    REGISTRY_PATH, approved_profession_history, load_approved_classifier_bytes,
)
from app.services.payroll_d5_classifier_guard import D5ClassifierError
from app.services.payroll_d5_trusted_classifier import import_approved_classifier
from scripts.build_d5_2026_registry import registry_bytes
from scripts.reconcile_d5_2026_classifier import EVIDENCE


def lookup(code, title, day):
    return approved_profession_history(profession_code=code, professional_name=title, event_date=day)


def test_shipped_registry_is_reproducible_and_independently_pinned():
    raw = load_approved_classifier_bytes()
    assert raw == registry_bytes()
    entries = import_approved_classifier(raw)
    assert len(entries) == 9160
    assert sum(e.effective_to is None for e in entries) == 9151
    assert sum(e.effective_from == date(2026, 1, 1) for e in entries) == 9145
    with pytest.raises(D5ClassifierError, match='approval'):
        import_approved_classifier(raw + b' ')


@pytest.mark.parametrize('operation', json.loads((EVIDENCE/'amendment_17_operations.json').read_text())['A'])
def test_every_amendment_17_operation_at_both_sides_of_boundary(operation):
    if operation[0] in ('remove', 'replace'):
        old_code, old_name = operation[1:3]
        assert lookup(old_code, old_name, date(2026, 4, 13)).periods[0].valid_to == date(2026, 4, 13)
        with pytest.raises(D5ClassifierError, match='effective profession'):
            lookup(old_code, old_name, date(2026, 4, 14))
    if operation[0] in ('add', 'replace'):
        code, name = operation[-2:]
        with pytest.raises(D5ClassifierError, match='effective profession'):
            lookup(code, name, date(2026, 4, 13))
        assert lookup(code, name, date(2026, 4, 14)).periods[0].valid_from == date(2026, 4, 14)


def test_unchanged_profession_and_coverage_boundary():
    for day in (date(2026, 1, 1), date(2026, 4, 13), date(2026, 4, 14)):
        period = lookup('3433', 'Бухгалтер', day).periods[0]
        assert period.valid_from == date(2026, 1, 1)
        assert period.valid_to is None
    with pytest.raises(D5ClassifierError, match='before 2026'):
        lookup('3433', 'Бухгалтер', date(2025, 12, 31))


def test_exact_identity_does_not_confuse_other_professions_in_same_group():
    entries = import_approved_classifier(load_approved_classifier_bytes())
    group = [e for e in entries if e.profession_code == '3433' and e.effective_to is None]
    assert len(group) > 1
    for entry in group:
        assert lookup(entry.profession_code, entry.professional_name, date(2026, 4, 14)).periods[0].title == entry.professional_name
    with pytest.raises(D5ClassifierError):
        lookup('3433', 'Invented profession', date(2026, 4, 14))


def test_military_scope_and_non_date_are_rejected():
    with pytest.raises(D5ClassifierError, match='Military'):
        lookup('0141', 'Any military title', date(2026, 4, 14))
    with pytest.raises(D5ClassifierError):
        lookup('3433', 'Бухгалтер', '2026-04-14')


def test_loader_rechecks_file_integrity(monkeypatch, tmp_path):
    import app.services.payroll_d5_approved_registry as module
    path = tmp_path/'tampered.json'
    path.write_bytes(REGISTRY_PATH.read_bytes() + b' ')
    monkeypatch.setattr(module, 'REGISTRY_PATH', path)
    with pytest.raises(D5ClassifierError, match='approval'):
        module.load_approved_classifier_bytes()
