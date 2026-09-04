from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.sales_return_cost_restoration_accounting_service import (
    SalesReturnCostRestorationAccountingError,
    create_sales_return_cost_restoration_accounting_plan,
    required_roles_for_sales_return_cost_restoration_plan,
)


def test_original_cost_restoration_is_dr_inventory_cr_cogs():
    plan = (
        create_sales_return_cost_restoration_accounting_plan(
            Decimal(
                "120.00"
            )
        )
    )

    assert plan.amount == Decimal(
        "120.00"
    )

    assert len(
        plan.lines
    ) == 2

    debit = plan.lines[
        0
    ]

    credit = plan.lines[
        1
    ]

    assert (
        debit.role
        == AccountingAccountRole.INVENTORY_GOODS
    )

    assert debit.debit == Decimal(
        "120.00"
    )

    assert debit.credit == Decimal(
        "0"
    )

    assert (
        credit.role
        == AccountingAccountRole.GOODS_COGS
    )

    assert credit.debit == Decimal(
        "0"
    )

    assert credit.credit == Decimal(
        "120.00"
    )


def test_required_roles_are_inventory_and_cogs():
    plan = (
        create_sales_return_cost_restoration_accounting_plan(
            Decimal(
                "10.00"
            )
        )
    )

    assert (
        required_roles_for_sales_return_cost_restoration_plan(
            plan
        )
        == (
            AccountingAccountRole.INVENTORY_GOODS,
            AccountingAccountRole.GOODS_COGS,
        )
    )


@pytest.mark.parametrize(
    "value",
    (
        Decimal(
            "0"
        ),
        Decimal(
            "-0.01"
        ),
    ),
)
def test_nonpositive_posting_amount_is_rejected(
    value,
):
    with pytest.raises(
        SalesReturnCostRestorationAccountingError
    ):
        create_sales_return_cost_restoration_accounting_plan(
            value
        )


def test_nonfinite_posting_amount_is_rejected():
    with pytest.raises(
        SalesReturnCostRestorationAccountingError,
        match="finite",
    ):
        create_sales_return_cost_restoration_accounting_plan(
            Decimal(
                "NaN"
            )
        )
