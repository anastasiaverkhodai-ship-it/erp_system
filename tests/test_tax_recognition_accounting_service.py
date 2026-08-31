from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.tax_recognition_accounting_service import (
    OutputVatRecognitionSourceKind,
    TaxRecognitionAccountingAmountError,
    TaxRecognitionAccountingSourceError,
    create_output_vat_recognition_accounting_plan,
    money,
    required_roles_for_output_vat_plan,
)


def test_fulfillment_first_output_vat_plan():
    plan = (
        create_output_vat_recognition_accounting_plan(
            source_kind=(
                OutputVatRecognitionSourceKind
                .FULFILLMENT
            ),
            amount=Decimal(
                "20.00"
            ),
        )
    )

    assert len(
        plan.lines
    ) == 2

    debit = plan.lines[0]
    credit = plan.lines[1]

    assert (
        debit.role
        == AccountingAccountRole.GOODS_REVENUE
    )

    assert debit.debit == Decimal(
        "20.00"
    )

    assert debit.credit == Decimal(
        "0.00"
    )

    assert (
        credit.role
        == AccountingAccountRole.TAX_SETTLEMENT
    )

    assert credit.debit == Decimal(
        "0.00"
    )

    assert credit.credit == Decimal(
        "20.00"
    )


def test_settlement_first_output_vat_plan():
    plan = (
        create_output_vat_recognition_accounting_plan(
            source_kind=(
                OutputVatRecognitionSourceKind
                .SETTLEMENT
            ),
            amount=Decimal(
                "20.00"
            ),
        )
    )

    assert len(
        plan.lines
    ) == 2

    debit = plan.lines[0]
    credit = plan.lines[1]

    assert (
        debit.role
        == AccountingAccountRole.VAT_OUTPUT
    )

    assert debit.debit == Decimal(
        "20.00"
    )

    assert debit.credit == Decimal(
        "0.00"
    )

    assert (
        credit.role
        == AccountingAccountRole.TAX_SETTLEMENT
    )

    assert credit.debit == Decimal(
        "0.00"
    )

    assert credit.credit == Decimal(
        "20.00"
    )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0.00"),
        Decimal("-0.01"),
        Decimal("-100.00"),
    ],
)
def test_nonpositive_output_vat_amount_fails_closed(
    amount,
):
    with pytest.raises(
        TaxRecognitionAccountingAmountError
    ):
        create_output_vat_recognition_accounting_plan(
            source_kind=(
                OutputVatRecognitionSourceKind
                .SETTLEMENT
            ),
            amount=amount,
        )


def test_invalid_source_kind_fails_closed():
    with pytest.raises(
        TaxRecognitionAccountingSourceError
    ):
        create_output_vat_recognition_accounting_plan(
            source_kind="settlement",
            amount=Decimal(
                "20.00"
            ),
        )


def test_required_roles_for_fulfillment():
    plan = (
        create_output_vat_recognition_accounting_plan(
            source_kind=(
                OutputVatRecognitionSourceKind
                .FULFILLMENT
            ),
            amount=Decimal(
                "20.00"
            ),
        )
    )

    assert (
        required_roles_for_output_vat_plan(
            plan
        )
        == (
            AccountingAccountRole.GOODS_REVENUE,
            AccountingAccountRole.TAX_SETTLEMENT,
        )
    )


def test_required_roles_for_settlement():
    plan = (
        create_output_vat_recognition_accounting_plan(
            source_kind=(
                OutputVatRecognitionSourceKind
                .SETTLEMENT
            ),
            amount=Decimal(
                "20.00"
            ),
        )
    )

    assert (
        required_roles_for_output_vat_plan(
            plan
        )
        == (
            AccountingAccountRole.VAT_OUTPUT,
            AccountingAccountRole.TAX_SETTLEMENT,
        )
    )


def test_money_quantizes_to_accounting_precision():
    assert money(
        Decimal(
            "20.005"
        )
    ) == Decimal(
        "20.01"
    )
