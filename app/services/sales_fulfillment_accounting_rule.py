from app.models.accounting_rule import AccountingRule
from app.models.accounting_rule_line import (
    AccountingAmountSource,
    AccountingRuleSide,
)
from app.models.document import DocumentType


class SalesFulfillmentAccountingRuleValidationError(
    Exception
):
    """
    Pure semantic validation error for the accounting rule
    used by Sales warehouse fulfillment.
    """


def validate_sales_fulfillment_accounting_rule(
    *,
    accounting_rule: AccountingRule,
    cogs_account_id: int,
    inventory_account_id: int,
) -> None:
    """
    Require an inventory-only accounting rule for Sales
    fulfillment.

    Warehouse ISSUE may recognize only:
      Dr GOODS_COGS
      Cr INVENTORY_GOODS

    Both amounts must come from INVENTORY_COST.

    Commercial AR, revenue and VAT belong to separate
    business lifecycles.
    """

    if cogs_account_id <= 0:
        raise ValueError(
            "cogs_account_id must be greater than zero"
        )

    if inventory_account_id <= 0:
        raise ValueError(
            "inventory_account_id must be greater than zero"
        )

    if cogs_account_id == inventory_account_id:
        raise ValueError(
            "COGS and inventory accounts must be different"
        )

    if (
        accounting_rule.document_type
        != DocumentType.ISSUE
    ):
        raise (
            SalesFulfillmentAccountingRuleValidationError(
                "Sales fulfillment accounting rule must "
                "be an ISSUE rule"
            )
        )

    if not accounting_rule.is_active:
        raise (
            SalesFulfillmentAccountingRuleValidationError(
                "Sales fulfillment accounting rule "
                "must be active"
            )
        )

    lines = tuple(
        accounting_rule.lines
    )

    expected = {
        (
            cogs_account_id,
            AccountingRuleSide.DEBIT,
            AccountingAmountSource.INVENTORY_COST,
        ),
        (
            inventory_account_id,
            AccountingRuleSide.CREDIT,
            AccountingAmountSource.INVENTORY_COST,
        ),
    }

    actual = {
        (
            line.account_id,
            line.side,
            line.amount_source,
        )
        for line in lines
    }

    if (
        len(lines) != 2
        or actual != expected
    ):
        raise (
            SalesFulfillmentAccountingRuleValidationError(
                "Sales fulfillment accounting rule must "
                "contain exactly Dr GOODS_COGS / "
                "Cr INVENTORY_GOODS using INVENTORY_COST "
                "and no commercial AR/revenue lines"
            )
        )
