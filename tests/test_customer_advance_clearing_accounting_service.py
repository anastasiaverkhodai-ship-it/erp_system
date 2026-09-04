from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.customer_advance_clearing_accounting_service import (
    CustomerAdvanceClearingAccountingError,
    create_customer_advance_clearing_accounting_plan,
    required_roles_for_customer_advance_clearing_plan,
)


ZERO = Decimal("0")


def test_plan_is_dr_payables_cr_supplier_advances():
    plan = (
        create_customer_advance_clearing_accounting_plan(
            amount=Decimal("120.00"),
        )
    )

    assert len(
        plan.lines
    ) == 2

    debit_line = (
        plan.lines[0]
    )

    credit_line = (
        plan.lines[1]
    )

    assert (
        debit_line.role
        == AccountingAccountRole.CUSTOMER_ADVANCES
    )

    assert (
        debit_line.debit
        == Decimal("120.00")
    )

    assert (
        debit_line.credit
        == ZERO
    )

    assert (
        credit_line.role
        == AccountingAccountRole.CUSTOMER_RECEIVABLES
    )

    assert (
        credit_line.debit
        == ZERO
    )

    assert (
        credit_line.credit
        == Decimal("120.00")
    )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0"),
        Decimal("-0.01"),
    ],
)
def test_plan_requires_positive_amount(
    amount,
):
    with pytest.raises(
        CustomerAdvanceClearingAccountingError,
        match="greater than zero",
    ):
        create_customer_advance_clearing_accounting_plan(
            amount=amount,
        )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_plan_requires_finite_amount(
    amount,
):
    with pytest.raises(
        CustomerAdvanceClearingAccountingError,
        match="finite",
    ):
        create_customer_advance_clearing_accounting_plan(
            amount=amount,
        )


def test_required_roles_are_supplier_payables_then_advances():
    plan = (
        create_customer_advance_clearing_accounting_plan(
            amount=Decimal("1.00"),
        )
    )

    assert (
        required_roles_for_customer_advance_clearing_plan(
            plan
        )
        == (
            AccountingAccountRole.CUSTOMER_ADVANCES,
            AccountingAccountRole.CUSTOMER_RECEIVABLES,
        )
    )
