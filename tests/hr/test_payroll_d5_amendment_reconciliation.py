from datetime import date

import pytest

from app.services.payroll_d5_amendment_reconciliation import (
    D5AmendmentEvidence,
    D5AmendmentReconciliationError,
    reconcile_d5_amendments,
)
from app.services.payroll_d5_profession_effective_history import (
    D5ProfessionHistoryChange,
)


DATES = {
    1: date(2012, 9, 1),
    6: date(2017, 11, 1),
    7: date(2017, 11, 30),
}


def source(number, completeness="FULL_DOCUMENT_VERIFIED"):
    return D5AmendmentEvidence(
        amendment_number=number,
        effective_date=DATES[number],
        source_reference=f"TEST-AMENDMENT-{number}",
        source_sha256=f"{number}" * 64,
        document_completeness=completeness,
        profession_list_completeness=completeness,
    )


def change(number, code, kind):
    item = source(number)
    return D5ProfessionHistoryChange(
        profession_code=code,
        change_kind=kind,
        effective_date=item.effective_date,
        source_reference=item.source_reference,
        source_sha256=item.source_sha256,
    )


def restore_ten():
    return [
        change(7, f"TEST-{n:02d}", "restored")
        for n in range(10)
    ]


def review(amendments, changes, code="TEST-00"):
    return reconcile_d5_amendments(
        profession_code=code,
        reporting_date=date(2017, 12, 1),
        amendments=amendments,
        changes=changes,
    )


def test_ten_unique_restorations_structurally_recognized():
    result = review(
        [source(1), source(6), source(7)],
        restore_ten(),
    )
    assert result["amendment_7_restored_unique"] == 10
    assert result["unbound_profession_changes"] == 0
    assert result["internal_consistency"] == "CANDIDATE_CONSISTENT"
    assert result["complete_classifier_history"] == "NOT_VERIFIED"
    assert result["normative_approval"] == "NOT_APPROVED"
    assert result["official_generation"] == "BLOCKED"


def test_nine_restorations_blocked():
    result = review(
        [source(1), source(6), source(7)],
        restore_ten()[:9],
    )
    assert "AMENDMENT_7_TEN_RESTORATIONS_NOT_VERIFIED" in (
        result["blockers"]
    )


def test_partial_annex_blocks():
    result = review(
        [source(1, "PARTIAL"), source(6), source(7)],
        restore_ten(),
    )
    assert "AMENDMENT_1_DOCUMENT_INCOMPLETE" in result["blockers"]
    assert "AMENDMENT_1_PROFESSIONS_INCOMPLETE" in result["blockers"]


def test_unbound_change_blocks():
    rogue = D5ProfessionHistoryChange(
        profession_code="OTHER",
        change_kind="introduced",
        effective_date=date(2020, 1, 1),
        source_reference="MISSING",
        source_sha256="a" * 64,
    )

    result = review(
        [source(1), source(6), source(7)],
        [*restore_ten(), rogue],
    )
    assert result["unbound_profession_changes"] == 1
    assert "UNBOUND_PROFESSION_CHANGE" in result["blockers"]


def test_wrong_amendment_six_date_rejected():
    bad = D5AmendmentEvidence(
        amendment_number=6,
        effective_date=date(2017, 10, 26),
        source_reference="TEST-WRONG-DATE",
        source_sha256="6" * 64,
        document_completeness="FULL_DOCUMENT_VERIFIED",
        profession_list_completeness="FULL_DOCUMENT_VERIFIED",
    )
    with pytest.raises(
        D5AmendmentReconciliationError,
        match="effective date",
    ):
        review([source(1), bad, source(7)], restore_ten())


def test_duplicate_amendment_rejected():
    with pytest.raises(
        D5AmendmentReconciliationError,
        match="Duplicate amendment",
    ):
        review(
            [source(1), source(1), source(6), source(7)],
            restore_ten(),
        )


def test_missing_amendment_six_blocked():
    result = review(
        [source(1), source(7)],
        restore_ten(),
    )
    assert "AMENDMENT_6_MISSING" in result["blockers"]


def test_cross_source_hash_mismatch_blocks():
    incorrect = D5ProfessionHistoryChange(
        profession_code="OTHER",
        change_kind="introduced",
        effective_date=DATES[7],
        source_reference="TEST-AMENDMENT-7",
        source_sha256="f" * 64,
    )
    result = review(
        [source(1), source(6), source(7)],
        [*restore_ten(), incorrect],
    )
    assert result["unbound_profession_changes"] == 1


def test_reconciliation_digest_stable():
    items = restore_ten()
    first = review(
        [source(1), source(6), source(7)],
        items,
    )
    second = review(
        [source(7), source(1), source(6)],
        list(reversed(items)),
    )
    assert first["reconciliation_sha256"] == (
        second["reconciliation_sha256"]
    )
