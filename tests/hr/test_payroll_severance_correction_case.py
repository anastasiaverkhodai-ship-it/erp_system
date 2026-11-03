from datetime import date

import pytest

from app.services.payroll_severance_correction_case_service import (
    SeveranceCorrectionCaseError,
    validate_case_request,
)


def valid():
    return dict(
        company_id=1,
        payroll_calculation_id=2,
        as_of=date(2026, 9, 30),
        evidence_date=date(2026, 9, 20),
        evidence_reference="CORRECTION-DOCUMENT-2026-001",
        request_key="severance-correction-001",
        actor_id=3,
    )


def test_documented_request():
    assert validate_case_request(**valid())[
        "payroll_calculation_id"
    ] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id", 0),
        ("company_id", True),
        ("payroll_calculation_id", -1),
        ("actor_id", 0),
        ("as_of", "2026-09-30"),
        ("evidence_date", "2026-09-20"),
        ("evidence_reference", "short"),
        ("request_key", "x"),
    ],
)
def test_bad_requests_fail_closed(field, value):
    payload = valid()
    payload[field] = value

    with pytest.raises(SeveranceCorrectionCaseError):
        validate_case_request(**payload)


def test_future_evidence_rejected():
    payload = valid()
    payload["evidence_date"] = date(2026, 10, 1)

    with pytest.raises(
        SeveranceCorrectionCaseError,
        match="after assessment",
    ):
        validate_case_request(**payload)


def test_exact_severance_case_source():
    from types import SimpleNamespace
    from decimal import Decimal as D
    from app.services.payroll_severance_correction_case_service import (
        require_exact_severance_case_source,
    )

    calc = SimpleNamespace(
        id=20, company_id=1, payroll_input_id=30
    )
    assessment = SimpleNamespace(
        company_id=1,
        payroll_input_id=30,
        last_calculated_source_id=40,
        original_gross=D("12000.00"),
    )
    line = SimpleNamespace(
        company_id=1,
        payroll_calculation_id=20,
        line_type="severance",
        source_termination_severance_id=40,
        amount=D("12000.00"),
    )

    assert require_exact_severance_case_source(
        calculation=calc,
        assessment=assessment,
        severance_lines=[line],
    ) == 40


@pytest.mark.parametrize(
    "change",
    [
        {"source_termination_severance_id": 41},
        {"amount": "12000.01"},
        {"payroll_calculation_id": 21},
        {"company_id": 2},
        {"line_type": "salary"},
    ],
)
def test_wrong_source_line_is_rejected(change):
    from types import SimpleNamespace
    from decimal import Decimal as D
    from app.services.payroll_severance_correction_case_service import (
        require_exact_severance_case_source,
    )

    calc = SimpleNamespace(
        id=20, company_id=1, payroll_input_id=30
    )
    assessment = SimpleNamespace(
        company_id=1,
        payroll_input_id=30,
        last_calculated_source_id=40,
        original_gross=D("12000.00"),
    )

    values = dict(
        company_id=1,
        payroll_calculation_id=20,
        line_type="severance",
        source_termination_severance_id=40,
        amount=D("12000.00"),
    )
    values.update(change)

    with pytest.raises(SeveranceCorrectionCaseError):
        require_exact_severance_case_source(
            calculation=calc,
            assessment=assessment,
            severance_lines=[SimpleNamespace(**values)],
        )


def test_missing_or_duplicate_severance_lines_rejected():
    from types import SimpleNamespace
    from decimal import Decimal as D
    from app.services.payroll_severance_correction_case_service import (
        require_exact_severance_case_source,
    )

    calc = SimpleNamespace(
        id=20, company_id=1, payroll_input_id=30
    )
    assessment = SimpleNamespace(
        company_id=1,
        payroll_input_id=30,
        last_calculated_source_id=40,
        original_gross=D("12000.00"),
    )
    line = SimpleNamespace(
        company_id=1,
        payroll_calculation_id=20,
        line_type="severance",
        source_termination_severance_id=40,
        amount=D("12000.00"),
    )

    for lines in ([], [line, line]):
        with pytest.raises(
            SeveranceCorrectionCaseError,
            match="Exactly one",
        ):
            require_exact_severance_case_source(
                calculation=calc,
                assessment=assessment,
                severance_lines=lines,
            )
