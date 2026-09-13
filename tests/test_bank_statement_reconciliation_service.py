import ast
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.bank_statement_line import (
    BankStatementLine,
)
from app.services.bank_statement_reconciliation_service import (
    BankStatementReconciliationAmountError,
    BankStatementReconciliationCurrencyError,
    expected_payment_direction,
    normalize_reconciliation_amount,
    normalize_reconciliation_currency,
)
from app.services.payment_types import (
    PaymentDirection,
)


def test_currency_normalization():
    assert (
        normalize_reconciliation_currency(
            " uah "
        )
        == "UAH"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "UA",
        "UAHH",
        "12A",
    ],
)
def test_invalid_currency_rejected(value):
    with pytest.raises(
        BankStatementReconciliationCurrencyError
    ):
        normalize_reconciliation_currency(
            value
        )


def test_amount_normalization():
    assert (
        normalize_reconciliation_amount(
            amount=Decimal("10"),
            currency_code="UAH",
        )
        == Decimal("10.00")
    )


@pytest.mark.parametrize(
    "value",
    [
        Decimal("0"),
        Decimal("-1"),
    ],
)
def test_non_positive_match_amount_rejected(
    value,
):
    with pytest.raises(
        BankStatementReconciliationAmountError
    ):
        normalize_reconciliation_amount(
            amount=value,
            currency_code="UAH",
        )


def test_positive_line_maps_to_incoming():
    line = BankStatementLine(
        amount=Decimal("100.00")
    )

    assert (
        expected_payment_direction(line)
        == PaymentDirection.INCOMING
    )


def test_negative_line_maps_to_outgoing():
    line = BankStatementLine(
        amount=Decimal("-100.00")
    )

    assert (
        expected_payment_direction(line)
        == PaymentDirection.OUTGOING
    )


def _service_ast():
    path = Path(
        "app/services/"
        "bank_statement_reconciliation_service.py"
    )

    return ast.parse(
        path.read_text()
    )


def test_service_does_not_own_transaction_or_gl():
    tree = _service_ast()

    calls = set()
    imports = set()

    for node in ast.walk(tree):
        if isinstance(
            node,
            ast.ImportFrom,
        ):
            if node.module:
                imports.add(
                    node.module
                )

            for alias in node.names:
                imports.add(
                    alias.name
                )

        elif isinstance(
            node,
            ast.Import,
        ):
            for alias in node.names:
                imports.add(
                    alias.name
                )

        elif isinstance(
            node,
            ast.Call,
        ):
            if isinstance(
                node.func,
                ast.Name,
            ):
                calls.add(
                    node.func.id
                )

            elif isinstance(
                node.func,
                ast.Attribute,
            ):
                calls.add(
                    node.func.attr
                )

    assert "commit" not in calls
    assert "rollback" not in calls

    for forbidden in (
        "JournalEntry",
        "create_payment_draft",
        "confirm_payment",
        "settle_payment",
        "generate_and_post_payment_journal_entry",
    ):
        assert forbidden not in imports
        assert forbidden not in calls


def test_service_has_no_historical_state_mutation():
    tree = _service_ast()

    forbidden_attributes = {
        "status",
        "reversed_by",
        "reversed_at",
        "reversal_of_id",
        "matched_amount",
        "currency_code",
        "payment_id",
        "bank_statement_line_id",
    }

    assignments = []

    for node in ast.walk(tree):
        if isinstance(
            node,
            ast.Assign,
        ):
            targets = node.targets

        elif isinstance(
            node,
            ast.AnnAssign,
        ):
            targets = [
                node.target
            ]

        else:
            continue

        for target in targets:
            if isinstance(
                target,
                ast.Attribute,
            ):
                assignments.append(
                    target.attr
                )

    assert not (
        forbidden_attributes
        & set(assignments)
    )


def test_reverse_service_uses_new_event_and_projection_delete():
    source = Path(
        "app/services/"
        "bank_statement_reconciliation_service.py"
    ).read_text()

    assert (
        "reverse_bank_statement_reconciliation"
        in source
    )
    assert (
        "reversal_of_id=original.id"
        in source
    )
    assert (
        "BankStatementReconciliationActiveLink"
        in source
    )
    assert (
        "delete("
        in source
    )


def test_create_service_creates_active_projection():
    source = Path(
        "app/services/"
        "bank_statement_reconciliation_service.py"
    ).read_text()

    assert (
        "reversal_of_id=None"
        in source
    )
    assert (
        "reconciliation_id=reconciliation.id"
        in source
    )
