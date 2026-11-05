"""Duration facts are validated without inventing statutory classifications."""
import ast
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.schemas.payroll_d6_evidence import D6Interval, D6EvidenceCreate
from app.models.payroll_d6_evidence_guards import D6_GUARD_SQL


def interval(**overrides):
    values=dict(start_date='2026-09-01',end_date='2026-09-05',basis_reference='Documented personnel assessment',
        unit='days',quantity='5',normal_quantity='30')
    return {**values,**overrides}


def test_partial_service_can_retain_full_period_normal_duration():
    parsed=D6Interval.model_validate(interval())
    assert parsed.quantity==5 and parsed.normal_quantity==30
    assert parsed.basis_code_candidate is None


@pytest.mark.parametrize('change',[{'quantity':'6'},{'quantity':'NaN'},{'end_date':'2026-08-31'},{'unit':'years'}])
def test_invalid_interval(change):
    with pytest.raises(ValidationError):
        D6Interval.model_validate(interval(**change))


def test_migration_and_metadata_install_identical_protection():
    migration=Path("alembic/versions/p13d6ev261104_d6_evidence.py").read_text()
    literals=[node.value for node in ast.walk(ast.parse(migration)) if isinstance(node,ast.Constant) and isinstance(node.value,str)]
    assert D6_GUARD_SQL in literals
