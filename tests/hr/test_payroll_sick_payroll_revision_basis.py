"""Corrected sick payroll basis must remain verifiable."""

from copy import deepcopy

import pytest

from test_payroll_sick_payroll_replay import scenario

from app.services.payroll_sick_payroll_replay import (
    replay_sick_payroll,
    SickPayrollReplayError,
)
from app.services.payroll_sick_payroll_revision_writer import (
    freeze_revision_assessment,
)
from app.services.payroll_correction_basis import (
    verify_basis,
)
from app.services.payroll_correction_source_evidence_service import (
    _fingerprint,
)


def revised():
    args = scenario("1500")
    gross, lines, assessment = replay_sick_payroll(**args)
    frozen = freeze_revision_assessment(assessment)

    calc = deepcopy(args["calculation"])
    calc.gross_amount = gross
    calc.minimum_wage_assessment = frozen

    from types import SimpleNamespace

    rows = [SimpleNamespace(**line) for line in lines]
    return calc, rows, frozen


def test_revision_basis_is_sealed_and_verifiable():
    calc, lines, frozen = revised()

    basis = frozen["correction_basis"]

    assert basis["parent_source_fingerprint"]
    assert basis["sha256"] == _fingerprint(
        basis["payload"]
    )

    verify_basis(calc, lines)


def test_revised_gross_tampering_is_rejected():
    calc, lines, frozen = revised()

    calc.gross_amount += 1

    with pytest.raises(Exception):
        verify_basis(calc, lines)


def test_revised_line_tampering_is_rejected():
    calc, lines, frozen = revised()

    lines[1].amount += 1

    with pytest.raises(Exception):
        verify_basis(calc, lines)


def test_revised_basis_fingerprint_tampering_is_rejected():
    calc, lines, frozen = revised()

    frozen["correction_basis"]["sha256"] = "0" * 64

    with pytest.raises(Exception):
        verify_basis(calc, lines)


def test_untrusted_basis_seal_is_rejected():
    args = scenario("1500")
    _, _, assessment = replay_sick_payroll(**args)

    assessment["correction_basis"]["seal"] = "fake"

    with pytest.raises(
        SickPayrollReplayError,
        match="Unsupported revision basis seal",
    ):
        freeze_revision_assessment(assessment)


def test_missing_original_fingerprint_is_rejected():
    args = scenario("1500")
    _, _, assessment = replay_sick_payroll(**args)

    del assessment["correction_basis"][
        "parent_source_fingerprint"
    ]

    with pytest.raises(
        SickPayrollReplayError,
        match="Original source fingerprint",
    ):
        freeze_revision_assessment(assessment)
