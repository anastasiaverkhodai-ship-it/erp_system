"""seed inventory-only sales fulfillment accounting rule

Revision ID: c3f5a71b9d20
Revises: 769e783aca5b

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f5a71b9d20"

down_revision: Union[
    str,
    Sequence[str],
    None,
] = "769e783aca5b"

branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None

depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


RULE_CODE = "SALES_FULFILLMENT_ISSUE"

RULE_NAME = (
    "Відпуск товарів покупцю: собівартість"
)


def _required_codes(
    template_type: str,
) -> tuple[str, str]:
    """
    Return:
      (COGS account code, inventory account code)
    """

    if template_type == "general_291":
        return (
            "902",
            "281",
        )

    if template_type == "simplified_186":
        return (
            "90",
            "26",
        )

    raise RuntimeError(
        "Unsupported Chart of Accounts template "
        f"for default Sales fulfillment rule: "
        f"{template_type!r}"
    )


def _load_required_account_id(
    bind,
    *,
    company_id: int,
    account_code: str,
    role_name: str,
) -> int:
    row = (
        bind.execute(
            sa.text(
                """
                SELECT id
                FROM accounts
                WHERE company_id = :company_id
                  AND code = :account_code
                  AND is_system IS TRUE
                  AND is_active IS TRUE
                  AND is_postable IS TRUE
                """
            ),
            {
                "company_id": company_id,
                "account_code": account_code,
            },
        )
        .mappings()
        .one_or_none()
    )

    if row is None:
        raise RuntimeError(
            "Cannot seed default Sales fulfillment "
            "accounting rule: required active, "
            "postable SYSTEM account is missing. "
            f"company_id={company_id}, "
            f"role={role_name}, "
            f"account_code={account_code}"
        )

    return int(
        row["id"]
    )


def _validate_existing_rule(
    bind,
    *,
    company_id: int,
    rule,
    cogs_account_id: int,
    inventory_account_id: int,
) -> None:
    if (
        rule["document_type"] != "issue"
        or not rule["is_active"]
    ):
        raise RuntimeError(
            "Existing SALES_FULFILLMENT_ISSUE "
            "rule has incompatible header semantics. "
            f"company_id={company_id}, "
            f"rule_id={rule['id']}"
        )

    lines = tuple(
        bind.execute(
            sa.text(
                """
                SELECT
                    account_id,
                    side,
                    amount_source
                FROM accounting_rule_lines
                WHERE accounting_rule_id = :rule_id
                ORDER BY line_no
                """
            ),
            {
                "rule_id": rule["id"],
            },
        )
        .mappings()
        .all()
    )

    actual = {
        (
            int(line["account_id"]),
            str(line["side"]),
            str(line["amount_source"]),
        )
        for line in lines
    }

    expected = {
        (
            cogs_account_id,
            "debit",
            "inventory_cost",
        ),
        (
            inventory_account_id,
            "credit",
            "inventory_cost",
        ),
    }

    if (
        len(lines) != 2
        or actual != expected
    ):
        raise RuntimeError(
            "Existing SALES_FULFILLMENT_ISSUE "
            "rule has incompatible line semantics. "
            "Expected exactly "
            "Dr COGS / Cr Inventory "
            "using INVENTORY_COST. "
            f"company_id={company_id}, "
            f"rule_id={rule['id']}"
        )


def upgrade() -> None:
    bind = op.get_bind()

    companies = tuple(
        bind.execute(
            sa.text(
                """
                SELECT
                    id,
                    chart_of_accounts_template
                FROM companies
                ORDER BY id
                FOR UPDATE
                """
            )
        )
        .mappings()
        .all()
    )

    for company in companies:
        company_id = int(
            company["id"]
        )

        template_type = str(
            company[
                "chart_of_accounts_template"
            ]
        )

        (
            cogs_code,
            inventory_code,
        ) = _required_codes(
            template_type
        )

        cogs_account_id = (
            _load_required_account_id(
                bind,
                company_id=company_id,
                account_code=cogs_code,
                role_name="GOODS_COGS",
            )
        )

        inventory_account_id = (
            _load_required_account_id(
                bind,
                company_id=company_id,
                account_code=inventory_code,
                role_name="INVENTORY_GOODS",
            )
        )

        existing = (
            bind.execute(
                sa.text(
                    """
                    SELECT
                        id,
                        document_type,
                        is_active
                    FROM accounting_rules
                    WHERE company_id = :company_id
                      AND code = :rule_code
                    FOR UPDATE
                    """
                ),
                {
                    "company_id": company_id,
                    "rule_code": RULE_CODE,
                },
            )
            .mappings()
            .one_or_none()
        )

        if existing is not None:
            _validate_existing_rule(
                bind,
                company_id=company_id,
                rule=existing,
                cogs_account_id=(
                    cogs_account_id
                ),
                inventory_account_id=(
                    inventory_account_id
                ),
            )

            continue

        rule_id = bind.execute(
            sa.text(
                """
                INSERT INTO accounting_rules (
                    company_id,
                    code,
                    name,
                    document_type,
                    is_active,
                    created_at
                )
                VALUES (
                    :company_id,
                    :rule_code,
                    :rule_name,
                    'issue',
                    TRUE,
                    CURRENT_TIMESTAMP
                )
                RETURNING id
                """
            ),
            {
                "company_id": company_id,
                "rule_code": RULE_CODE,
                "rule_name": RULE_NAME,
            },
        ).scalar_one()

        bind.execute(
            sa.text(
                """
                INSERT INTO accounting_rule_lines (
                    accounting_rule_id,
                    line_no,
                    account_id,
                    side,
                    amount_source,
                    description
                )
                VALUES
                (
                    :rule_id,
                    1,
                    :cogs_account_id,
                    'debit',
                    'inventory_cost',
                    :cogs_description
                ),
                (
                    :rule_id,
                    2,
                    :inventory_account_id,
                    'credit',
                    'inventory_cost',
                    :inventory_description
                )
                """
            ),
            {
                "rule_id": rule_id,
                "cogs_account_id": (
                    cogs_account_id
                ),
                "inventory_account_id": (
                    inventory_account_id
                ),
                "cogs_description": (
                    "Собівартість реалізованих товарів"
                ),
                "inventory_description": (
                    "Списання товарів зі складу"
                ),
            },
        )


def downgrade() -> None:
    """
    Intentionally preserve seeded accounting-rule data.

    Once an installation has used this rule, deleting it can
    break historical Document / JournalEntry references.

    Re-upgrade is safe because upgrade() is idempotent and
    validates an existing rule before accepting it.
    """
    pass
