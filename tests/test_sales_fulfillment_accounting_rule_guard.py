from app.models.accounting_rule import AccountingRule
from app.models.accounting_rule_line import (
    AccountingAmountSource,
    AccountingRuleLine,
    AccountingRuleSide,
)
from app.models.document import DocumentType
from app.services.sales_fulfillment_accounting_rule import (
    SalesFulfillmentAccountingRuleValidationError,
    validate_sales_fulfillment_accounting_rule,
)

COGS_ACCOUNT_ID = 11
INVENTORY_ACCOUNT_ID = 2
AR_ACCOUNT_ID = 7
REVENUE_ACCOUNT_ID = 9


def make_rule(
    *,
    lines,
    document_type=DocumentType.ISSUE,
    is_active=True,
):
    rule = AccountingRule(
        id=5,
        company_id=1,
        code="TEST_SALES_ISSUE",
        name="Test Sales Issue",
        document_type=document_type,
        is_active=is_active,
    )
    rule.lines = list(lines)
    return rule


def rule_line(
    *,
    line_no,
    account_id,
    side,
    amount_source,
):
    return AccountingRuleLine(
        line_no=line_no,
        account_id=account_id,
        side=side,
        amount_source=amount_source,
    )


def test_inventory_only_sales_rule_is_accepted():
    rule = make_rule(
        lines=[
            rule_line(
                line_no=1,
                account_id=COGS_ACCOUNT_ID,
                side=AccountingRuleSide.DEBIT,
                amount_source=(
                    AccountingAmountSource.INVENTORY_COST
                ),
            ),
            rule_line(
                line_no=2,
                account_id=INVENTORY_ACCOUNT_ID,
                side=AccountingRuleSide.CREDIT,
                amount_source=(
                    AccountingAmountSource.INVENTORY_COST
                ),
            ),
        ]
    )

    validate_sales_fulfillment_accounting_rule(
        accounting_rule=rule,
        cogs_account_id=COGS_ACCOUNT_ID,
        inventory_account_id=INVENTORY_ACCOUNT_ID,
    )


def test_legacy_combined_sales_rule_is_rejected():
    rule = make_rule(
        lines=[
            rule_line(
                line_no=1,
                account_id=AR_ACCOUNT_ID,
                side=AccountingRuleSide.DEBIT,
                amount_source=AccountingAmountSource.LINE_TOTAL,
            ),
            rule_line(
                line_no=2,
                account_id=REVENUE_ACCOUNT_ID,
                side=AccountingRuleSide.CREDIT,
                amount_source=AccountingAmountSource.LINE_TOTAL,
            ),
            rule_line(
                line_no=3,
                account_id=COGS_ACCOUNT_ID,
                side=AccountingRuleSide.DEBIT,
                amount_source=(
                    AccountingAmountSource.INVENTORY_COST
                ),
            ),
            rule_line(
                line_no=4,
                account_id=INVENTORY_ACCOUNT_ID,
                side=AccountingRuleSide.CREDIT,
                amount_source=(
                    AccountingAmountSource.INVENTORY_COST
                ),
            ),
        ]
    )

    try:
        validate_sales_fulfillment_accounting_rule(
            accounting_rule=rule,
            cogs_account_id=COGS_ACCOUNT_ID,
            inventory_account_id=INVENTORY_ACCOUNT_ID,
        )
    except SalesFulfillmentAccountingRuleValidationError:
        pass
    else:
        raise AssertionError(
            "Legacy combined Sales ISSUE rule was accepted"
        )


def test_sales_rule_with_wrong_amount_source_is_rejected():
    rule = make_rule(
        lines=[
            rule_line(
                line_no=1,
                account_id=COGS_ACCOUNT_ID,
                side=AccountingRuleSide.DEBIT,
                amount_source=AccountingAmountSource.LINE_TOTAL,
            ),
            rule_line(
                line_no=2,
                account_id=INVENTORY_ACCOUNT_ID,
                side=AccountingRuleSide.CREDIT,
                amount_source=(
                    AccountingAmountSource.INVENTORY_COST
                ),
            ),
        ]
    )

    try:
        validate_sales_fulfillment_accounting_rule(
            accounting_rule=rule,
            cogs_account_id=COGS_ACCOUNT_ID,
            inventory_account_id=INVENTORY_ACCOUNT_ID,
        )
    except SalesFulfillmentAccountingRuleValidationError:
        pass
    else:
        raise AssertionError(
            "Sales rule with LINE_TOTAL was accepted"
        )


def test_inactive_sales_rule_is_rejected():
    rule = make_rule(
        is_active=False,
        lines=[
            rule_line(
                line_no=1,
                account_id=COGS_ACCOUNT_ID,
                side=AccountingRuleSide.DEBIT,
                amount_source=(
                    AccountingAmountSource.INVENTORY_COST
                ),
            ),
            rule_line(
                line_no=2,
                account_id=INVENTORY_ACCOUNT_ID,
                side=AccountingRuleSide.CREDIT,
                amount_source=(
                    AccountingAmountSource.INVENTORY_COST
                ),
            ),
        ],
    )

    try:
        validate_sales_fulfillment_accounting_rule(
            accounting_rule=rule,
            cogs_account_id=COGS_ACCOUNT_ID,
            inventory_account_id=INVENTORY_ACCOUNT_ID,
        )
    except SalesFulfillmentAccountingRuleValidationError:
        pass
    else:
        raise AssertionError(
            "Inactive Sales fulfillment rule was accepted"
        )
