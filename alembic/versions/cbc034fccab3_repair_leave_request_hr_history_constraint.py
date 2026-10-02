"""repair leave request hr history constraint

Revision ID: cbc034fccab3
Revises: 3ff0ec5e17e5
Create Date: 2026-10-02
"""

from typing import Sequence, Union

from alembic import op


revision: str = "cbc034fccab3"
down_revision: Union[str, Sequence[str], None] = "3ff0ec5e17e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONSTRAINT_NAME = "ck_hr_changes_entity_type"

OLD_ENTITY_TYPES = (
    "employee",
    "department",
    "position",
    "employment_contract",
    "salary_rate",
    "work_schedule",
    "employment_schedule_assignment",
    "attendance_record",
)

NEW_ENTITY_TYPES = OLD_ENTITY_TYPES + (
    "leave_request",
)


def _expression(values: tuple[str, ...]) -> str:
    joined = ", ".join(
        f"'{value}'"
        for value in values
    )
    return f"entity_type IN ({joined})"


def upgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME,
        "hr_changes",
        type_="check",
    )

    op.create_check_constraint(
        CONSTRAINT_NAME,
        "hr_changes",
        _expression(NEW_ENTITY_TYPES),
    )


def downgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME,
        "hr_changes",
        type_="check",
    )

    op.create_check_constraint(
        CONSTRAINT_NAME,
        "hr_changes",
        _expression(OLD_ENTITY_TYPES),
    )
