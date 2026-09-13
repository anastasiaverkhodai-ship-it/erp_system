from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.cash_desk import CashDesk
from app.schemas.cash_desk import CashDeskCreate


def test_cash_desk_table_contract():
    table = CashDesk.__table__

    assert table.name == "cash_desks"
    assert {
        column.name
        for column in table.columns
    } == {
        "id",
        "company_id",
        "name",
        "code",
        "currency_code",
        "accounting_account_id",
        "is_active",
        "created_at",
        "updated_at",
    }


def test_cash_desk_company_identity_constraints():
    table = CashDesk.__table__

    unique_sets = {
        tuple(
            column.name
            for column in constraint.columns
        )
        for constraint in table.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "company_id",
        "id",
    ) in unique_sets

    assert (
        "company_id",
        "code",
    ) in unique_sets


def test_cash_desk_has_company_safe_account_fk():
    table = CashDesk.__table__

    composite_fks = []

    for constraint in table.constraints:
        if not isinstance(
            constraint,
            ForeignKeyConstraint,
        ):
            continue

        local = tuple(
            element.parent.name
            for element in constraint.elements
        )
        remote = tuple(
            element.target_fullname
            for element in constraint.elements
        )

        composite_fks.append(
            (local, remote)
        )

    assert (
        (
            "company_id",
            "accounting_account_id",
        ),
        (
            "accounts.company_id",
            "accounts.id",
        ),
    ) in composite_fks


def test_cash_desk_check_constraints():
    names = {
        constraint.name
        for constraint
        in CashDesk.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert "ck_cash_desks_name_nonempty" in names
    assert "ck_cash_desks_code_nonempty" in names
    assert (
        "ck_cash_desks_currency_code_length"
        in names
    )


def test_cash_desk_create_normalizes_values():
    payload = CashDeskCreate(
        name="  Main cash desk  ",
        code="  cash-main  ",
        currency_code="uah",
        accounting_account_id=10,
    )

    assert payload.name == "Main cash desk"
    assert payload.code == "CASH-MAIN"
    assert payload.currency_code == "UAH"
