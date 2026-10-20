"""Bind documented severance to immutable payroll calculation lines.

Revision ID: 13d4a7b8c041
Revises: 13d4a7b8c040
"""

from alembic import op
import sqlalchemy as sa

revision = "13d4a7b8c041"
down_revision = "13d4a7b8c040"
branch_labels = None
depends_on = None

POSITIVE = (
    "'salary','supplement','vacation_pay','sick_pay',"
    "'minimum_wage','leave_compensation','severance'"
)

ALL_TYPES = (
    "'salary','manual_adjustment','supplement',"
    "'vacation_pay','sick_pay','minimum_wage',"
    "'leave_compensation','severance'"
)

OLD_POSITIVE = (
    "'salary','supplement','vacation_pay','sick_pay',"
    "'minimum_wage','leave_compensation'"
)

OLD_TYPES = (
    "'salary','manual_adjustment','supplement',"
    "'vacation_pay','sick_pay','minimum_wage',"
    "'leave_compensation'"
)


def _replace_checks(positive, all_types):
    for name in (
        "ck_payroll_calculation_lines_type",
        "ck_payroll_calculation_lines_rate",
        "ck_payroll_calculation_lines_amount",
    ):
        op.drop_constraint(
            name, "payroll_calculation_lines", type_="check"
        )

    op.create_check_constraint(
        "ck_payroll_calculation_lines_type",
        "payroll_calculation_lines",
        f"line_type IN ({all_types})",
    )
    op.create_check_constraint(
        "ck_payroll_calculation_lines_rate",
        "payroll_calculation_lines",
        f"(line_type IN ({positive}) AND rate IS NOT NULL "
        "AND rate >= 0) OR "
        "(line_type = 'manual_adjustment' AND rate IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_payroll_calculation_lines_amount",
        "payroll_calculation_lines",
        f"(line_type IN ({positive}) AND amount >= 0) "
        "OR line_type = 'manual_adjustment'",
    )


def upgrade():
    op.add_column(
        "payroll_calculation_lines",
        sa.Column(
            "source_termination_severance_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_pcl_severance",
        "payroll_calculation_lines",
        "payroll_termination_severances",
        ["company_id", "source_termination_severance_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_pcl_severance_source",
        "payroll_calculation_lines",
        "(line_type = 'severance') = "
        "(source_termination_severance_id IS NOT NULL)",
    )

    _replace_checks(POSITIVE, ALL_TYPES)


def downgrade():
    # Historical severance payroll lines must never be discarded.
    op.execute("""
        LOCK TABLE payroll_calculation_lines
        IN ACCESS EXCLUSIVE MODE
    """)

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM payroll_calculation_lines
                WHERE source_termination_severance_id IS NOT NULL
                   OR line_type = 'severance'
            ) THEN
                RAISE EXCEPTION
                    'Cannot discard historical severance payroll links';
            END IF;
        END $$;
    """)

    _replace_checks(OLD_POSITIVE, OLD_TYPES)

    op.drop_constraint(
        "ck_pcl_severance_source",
        "payroll_calculation_lines",
        type_="check",
    )
    op.drop_constraint(
        "fk_pcl_severance",
        "payroll_calculation_lines",
        type_="foreignkey",
    )
    op.drop_column(
        "payroll_calculation_lines",
        "source_termination_severance_id",
    )
