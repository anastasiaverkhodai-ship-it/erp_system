from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from app.models.payroll import PayrollCalculationLine
from app.schemas.employment_termination_basis import (
    EmploymentTerminationBasis,
)
from app.services.payroll_termination_severance_policy import (
    classify_severance_basis,
)
from app.services.payroll_termination_severance_lines import (
    SeveranceLineError,
    build_severance_line,
    require_revision_chain,
)


def inputs():
    basis = EmploymentTerminationBasis(
        ground="redundancy",
        labour_code_provision="40.1",
        source_reference="Verified termination order",
    )
    policy = classify_severance_basis(basis)

    preflight = SimpleNamespace(
        company_id=5,
        payroll_input_id=10,
        employment_contract_id=30,
        termination_event_id=40,
        termination_date=date(2026, 9, 30),
        termination_order_number="TERM-001",
        policy=policy,
    )
    source = SimpleNamespace(
        id=50,
        company_id=5,
        payroll_input_id=10,
        employment_contract_id=30,
        termination_event_id=40,
        termination_date=date(2026, 9, 30),
        order_number="TERM-001",
        policy_ground="redundancy",
        policy_category="average_months",
        labour_code_provision="40.1",
        source_reference="Verified termination order",
        multiplier=1,
        basis_as_of=date(2026, 9, 30),
        basis_reference="Verified monthly average",
        base_amount=Decimal("12000.00"),
        gross_amount=Decimal("12000.00"),
        currency_code="UAH",
    )
    payroll_input = SimpleNamespace(
        id=10,
        employment_contract_id=30,
    )
    period = SimpleNamespace(
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
    )
    return source, preflight, basis, payroll_input, period


def line_for(values):
    source, preflight, basis, payroll_input, period = values
    return build_severance_line(
        source=source,
        preflight=preflight,
        termination_basis=basis,
        payroll_input=payroll_input,
        period=period,
    )


def test_positive_severance_line():
    line = line_for(inputs())

    assert line["line_type"] == "severance"
    assert line["amount"] == Decimal("12000.00")
    assert line["source_termination_severance_id"] == 50
    assert line["currency_code"] == "UAH"


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_id", 99),
        ("employment_contract_id", 99),
        ("termination_event_id", 99),
        ("termination_date", date(2026, 10, 1)),
        ("order_number", "WRONG"),
        ("policy_ground", "other"),
        ("policy_category", "minimum_wages"),
        ("labour_code_provision", "36.1"),
        ("source_reference", "Wrong evidence"),
        ("multiplier", 6),
        ("currency_code", "EUR"),
        ("gross_amount", Decimal("15000.00")),
        ("gross_amount", Decimal("NaN")),
        ("basis_as_of", date(2026, 10, 1)),
    ],
)
def test_bad_source_rejected(field, value):
    values = inputs()
    setattr(values[0], field, value)

    with pytest.raises(SeveranceLineError):
        line_for(values)


def test_revision_chain():
    first = SimpleNamespace(
        id=10, revision=1, supersedes_id=None
    )
    second = SimpleNamespace(
        id=11, revision=2, supersedes_id=10
    )

    require_revision_chain([first, second])

    with pytest.raises(SeveranceLineError):
        require_revision_chain([second])

    second.supersedes_id = 99

    with pytest.raises(SeveranceLineError):
        require_revision_chain([first, second])


def test_orm_source_fk_and_check():
    table = PayrollCalculationLine.__table__
    assert "source_termination_severance_id" in table.columns

    named = {
        constraint.name: constraint
        for constraint in table.constraints
        if constraint.name
    }

    assert isinstance(
        named["fk_pcl_severance"], ForeignKeyConstraint
    )
    assert isinstance(
        named["ck_pcl_severance_source"], CheckConstraint
    )

    assert "severance" in str(
        named["ck_payroll_calculation_lines_type"].sqltext
    )


def test_migration_chain():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_heads() == ["13d4a7b8c042"]
    assert (
        script.get_revision("13d4a7b8c041").down_revision
        == "13d4a7b8c040"
    )
