from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.payment_accounting_service import (
    PaymentAccountingAmountError,
    create_payment_confirmation_accounting_plan,
    create_settlement_accounting_plan,
    required_roles_for_plan,
)
from app.services.payment_types import (
    PaymentDirection,
)


ZERO = Decimal("0.00")


def by_role(plan):
    return {
        line.role: line
        for line
        in plan.lines
    }


def test_incoming_payment_confirmation_plan():
    plan = (
        create_payment_confirmation_accounting_plan(
            direction=(
                PaymentDirection.INCOMING
            ),
            amount=Decimal("100.00"),
        )
    )

    lines = by_role(
        plan
    )

    bank = lines[
        AccountingAccountRole.BANK_CURRENT_UAH
    ]

    advances = lines[
        AccountingAccountRole.CUSTOMER_ADVANCES
    ]

    assert bank.debit == Decimal("100.00")
    assert bank.credit == ZERO

    assert advances.debit == ZERO
    assert advances.credit == Decimal("100.00")


def test_outgoing_payment_confirmation_plan():
    plan = (
        create_payment_confirmation_accounting_plan(
            direction=(
                PaymentDirection.OUTGOING
            ),
            amount=Decimal("100.00"),
        )
    )

    lines = by_role(
        plan
    )

    advances = lines[
        AccountingAccountRole.SUPPLIER_ADVANCES
    ]

    bank = lines[
        AccountingAccountRole.BANK_CURRENT_UAH
    ]

    assert advances.debit == Decimal("100.00")
    assert advances.credit == ZERO

    assert bank.debit == ZERO
    assert bank.credit == Decimal("100.00")


def test_incoming_settlement_plan():
    plan = (
        create_settlement_accounting_plan(
            direction=(
                PaymentDirection.INCOMING
            ),
            amount=Decimal("70.00"),
        )
    )

    lines = by_role(
        plan
    )

    advances = lines[
        AccountingAccountRole.CUSTOMER_ADVANCES
    ]

    receivable = lines[
        AccountingAccountRole.CUSTOMER_RECEIVABLES
    ]

    assert advances.debit == Decimal("70.00")
    assert advances.credit == ZERO

    assert receivable.debit == ZERO
    assert receivable.credit == Decimal("70.00")


def test_outgoing_settlement_plan():
    plan = (
        create_settlement_accounting_plan(
            direction=(
                PaymentDirection.OUTGOING
            ),
            amount=Decimal("70.00"),
        )
    )

    lines = by_role(
        plan
    )

    payable = lines[
        AccountingAccountRole.SUPPLIER_PAYABLES
    ]

    advances = lines[
        AccountingAccountRole.SUPPLIER_ADVANCES
    ]

    assert payable.debit == Decimal("70.00")
    assert payable.credit == ZERO

    assert advances.debit == ZERO
    assert advances.credit == Decimal("70.00")


@pytest.mark.parametrize(
    "factory",
    (
        create_payment_confirmation_accounting_plan,
        create_settlement_accounting_plan,
    ),
)
def test_accounting_plan_rejects_nonpositive_amount(
    factory,
):
    with pytest.raises(
        PaymentAccountingAmountError
    ):
        factory(
            direction=(
                PaymentDirection.INCOMING
            ),
            amount=Decimal("0"),
        )


def test_required_roles_are_unique():
    plan = (
        create_payment_confirmation_accounting_plan(
            direction=(
                PaymentDirection.INCOMING
            ),
            amount=Decimal("100.00"),
        )
    )

    assert (
        required_roles_for_plan(
            plan
        )
        == (
            AccountingAccountRole.BANK_CURRENT_UAH,
            AccountingAccountRole.CUSTOMER_ADVANCES,
        )
    )
