"""Phase 13 C4-B2 ORM and Alembic contracts."""

from pathlib import Path

import sqlalchemy as sa

from app.core.database import Base
from app.models.payroll_deduction_remittance import (
    PayrollDeductionRemittancePlan,
    PayrollDeductionRemittanceEvent,
)


def test_tables_registered_in_metadata():
    assert (
        "payroll_deduction_remittance_plans"
        in Base.metadata.tables
    )
    assert (
        "payroll_deduction_remittance_events"
        in Base.metadata.tables
    )


def test_models_share_base_metadata():
    assert PayrollDeductionRemittancePlan.metadata is Base.metadata
    assert PayrollDeductionRemittanceEvent.metadata is Base.metadata


def test_plan_composite_fk():
    table = PayrollDeductionRemittancePlan.__table__

    fks = {
        fk.name: {
            element.target_fullname
            for element in fk.elements
        }
        for fk in table.foreign_key_constraints
    }

    assert fks["fk_pdrp_company_instruction"] == {
        "payroll_deduction_instructions.company_id",
        "payroll_deduction_instructions.id",
    }


def test_event_composite_fk():
    table = PayrollDeductionRemittanceEvent.__table__

    fks = {
        fk.name: {
            element.target_fullname
            for element in fk.elements
        }
        for fk in table.foreign_key_constraints
    }

    assert fks["fk_pdrevent_company_plan"] == {
        "payroll_deduction_remittance_plans.company_id",
        "payroll_deduction_remittance_plans.id",
    }


def test_company_scoped_uniqueness():
    plan = PayrollDeductionRemittancePlan.__table__
    event = PayrollDeductionRemittanceEvent.__table__

    def unique_names(table):
        return {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        }

    assert "uq_pdrp_company_instruction" in unique_names(plan)
    assert "uq_pdrp_company_id_id" in unique_names(plan)

    assert "uq_pdrevent_company_event" in unique_names(event)
    assert "uq_pdrevent_company_id_id" in unique_names(event)


def test_money_types():
    plan = PayrollDeductionRemittancePlan.__table__
    event = PayrollDeductionRemittanceEvent.__table__

    available = plan.c.available_amount.type
    amount = event.c.amount.type

    assert available.precision == 18
    assert available.scale == 2

    assert amount.precision == 18
    assert amount.scale == 2


def test_database_constraints():
    plan = PayrollDeductionRemittancePlan.__table__
    event = PayrollDeductionRemittanceEvent.__table__

    def checks(table):
        return {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, sa.CheckConstraint)
        }

    assert "ck_pdrp_available_nonnegative" in checks(plan)
    assert "ck_pdrp_currency" in checks(plan)
    assert "ck_pdrp_beneficiary" in checks(plan)

    assert "ck_pdrevent_positive_amount" in checks(event)
    assert "ck_pdrevent_event_id" in checks(event)


def test_migration_contains_immutable_triggers():
    path = Path(
        "alembic/versions/"
        "13d4a7b8c024_payroll_deduction_remittance_schema.py"
    )

    source = path.read_text(encoding="utf-8")

    assert 'revision = "13d4a7b8c024"' in source
    assert 'down_revision = "13d4a7b8c023"' in source

    assert "trg_pdrp_immutable" in source
    assert "trg_pdrevent_immutable" in source

    assert "BEFORE UPDATE OR DELETE" in source
    assert "DROP FUNCTION pdrm_reject_mutation" in source


def test_migration_has_no_data_transfer():
    path = Path(
        "alembic/versions/"
        "13d4a7b8c024_payroll_deduction_remittance_schema.py"
    )

    source = path.read_text(encoding="utf-8")

    assert "INSERT INTO" not in source
    assert "UPDATE payroll_" not in source
    assert "DELETE FROM payroll_" not in source


def test_remittance_is_not_payment_execution():
    from app.services.payroll_deduction_remittance_rules import (
        open_remittance_plan,
        reconcile_remittance_plan,
    )

    from decimal import Decimal

    plan = open_remittance_plan(
        company_id=1,
        instruction_id=1,
        beneficiary_reference="TEST",
        currency_code="UAH",
        available_amount=Decimal("100.00"),
    )

    balance = reconcile_remittance_plan(plan)

    assert balance.payment_execution_allowed is False
    assert balance.payroll_posting_allowed is False
    assert balance.legal_authorization == "NOT_AUTHORIZED"
