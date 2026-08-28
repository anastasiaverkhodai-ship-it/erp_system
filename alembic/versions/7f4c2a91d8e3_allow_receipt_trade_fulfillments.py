"""allow receipt trade fulfillments

Revision ID: 7f4c2a91d8e3
Revises: 152e2100aa38
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op


revision: str = "7f4c2a91d8e3"
down_revision: str | None = "152e2100aa38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_CHECK = (
    "ck_trade_fulfillment_"
    "warehouse_document_issue"
)

NEW_CHECK = (
    "ck_trade_fulfillment_"
    "warehouse_document_type"
)


def upgrade() -> None:
    # TradeFulfillment becomes direction-neutral:
    #
    #   SALE ORDER     -> ISSUE
    #   PURCHASE ORDER -> RECEIPT
    #
    # Existing rows remain valid because they are ISSUE rows.
    op.drop_constraint(
        OLD_CHECK,
        "trade_fulfillments",
        type_="check",
    )

    op.create_check_constraint(
        NEW_CHECK,
        "trade_fulfillments",
        (
            "warehouse_document_type "
            "IN ('issue', 'receipt')"
        ),
    )

    # Require every executor to specify the target type
    # explicitly. Existing persisted values are unchanged.
    op.alter_column(
        "trade_fulfillments",
        "warehouse_document_type",
        server_default=None,
    )


def downgrade() -> None:
    # Downgrade is unsafe if PURCHASE -> RECEIPT fulfillment
    # rows already exist. PostgreSQL will reject the old
    # ISSUE-only constraint in that case, which is intentional.
    op.drop_constraint(
        NEW_CHECK,
        "trade_fulfillments",
        type_="check",
    )

    op.create_check_constraint(
        OLD_CHECK,
        "trade_fulfillments",
        "warehouse_document_type = 'issue'",
    )

    op.alter_column(
        "trade_fulfillments",
        "warehouse_document_type",
        server_default="issue",
    )
