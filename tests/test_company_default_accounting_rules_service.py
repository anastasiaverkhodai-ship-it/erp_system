from types import SimpleNamespace

import pytest

from app.models.accounting_rule import AccountingRule
from app.models.accounting_rule_line import (
    AccountingAmountSource,
    AccountingRuleLine,
    AccountingRuleSide,
)
from app.models.document import DocumentType
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.company_default_accounting_rules_service import (
    SALES_FULFILLMENT_RULE_CODE,
    CompanyDefaultAccountingRulesConflictError,
    seed_company_default_accounting_rules,
)


class FakeResult:
    def __init__(
        self,
        value,
    ):
        self.value = value

    def scalar_one_or_none(
        self,
    ):
        return self.value


class FakeSession:
    def __init__(
        self,
        execute_values,
    ):
        self.execute_values = list(
            execute_values
        )
        self.added = []
        self.flush_count = 0

    async def execute(
        self,
        statement,
    ):
        if not self.execute_values:
            raise AssertionError(
                "Unexpected session.execute()"
            )

        return FakeResult(
            self.execute_values.pop(0)
        )

    def add(
        self,
        obj,
    ):
        self.added.append(
            obj
        )

    async def flush(
        self,
    ):
        self.flush_count += 1

        for index, obj in enumerate(
            self.added,
            start=100,
        ):
            if getattr(
                obj,
                "id",
                None,
            ) is None:
                obj.id = index


def make_role_accounts():
    return {
        AccountingAccountRole.GOODS_COGS:
            SimpleNamespace(
                id=11,
            ),
        AccountingAccountRole.INVENTORY_GOODS:
            SimpleNamespace(
                id=2,
            ),
    }


@pytest.mark.asyncio
async def test_seed_creates_inventory_only_rule(
    monkeypatch,
):
    import app.services.company_default_accounting_rules_service as service

    async def fake_resolve(
        session,
        *,
        company_id,
        roles,
    ):
        assert company_id == 1

        assert tuple(
            roles
        ) == (
            AccountingAccountRole.GOODS_COGS,
            AccountingAccountRole.INVENTORY_GOODS,
        )

        return make_role_accounts()

    monkeypatch.setattr(
        service,
        "resolve_company_account_roles",
        fake_resolve,
    )

    db = FakeSession(
        execute_values=[
            SimpleNamespace(
                id=1,
            ),
            None,
        ]
    )

    result = (
        await seed_company_default_accounting_rules(
            session=db,
            company_id=1,
        )
    )

    assert len(
        result
    ) == 1

    rule = result[0]

    assert isinstance(
        rule,
        AccountingRule,
    )

    assert (
        rule.code
        == SALES_FULFILLMENT_RULE_CODE
    )

    assert (
        rule.document_type
        == DocumentType.ISSUE
    )

    assert rule.is_active is True

    assert len(
        rule.lines
    ) == 2

    assert (
        rule.lines[0].account_id,
        rule.lines[0].side,
        rule.lines[0].amount_source,
    ) == (
        11,
        AccountingRuleSide.DEBIT,
        AccountingAmountSource.INVENTORY_COST,
    )

    assert (
        rule.lines[1].account_id,
        rule.lines[1].side,
        rule.lines[1].amount_source,
    ) == (
        2,
        AccountingRuleSide.CREDIT,
        AccountingAmountSource.INVENTORY_COST,
    )

    assert db.added == [
        rule,
    ]

    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_seed_is_idempotent_for_compatible_rule(
    monkeypatch,
):
    import app.services.company_default_accounting_rules_service as service

    async def fake_resolve(
        session,
        *,
        company_id,
        roles,
    ):
        return make_role_accounts()

    monkeypatch.setattr(
        service,
        "resolve_company_account_roles",
        fake_resolve,
    )

    existing = AccountingRule(
        id=55,
        company_id=1,
        code=SALES_FULFILLMENT_RULE_CODE,
        name="Existing",
        document_type=DocumentType.ISSUE,
        is_active=True,
    )

    existing.lines = [
        AccountingRuleLine(
            line_no=1,
            account_id=11,
            side=AccountingRuleSide.DEBIT,
            amount_source=(
                AccountingAmountSource.INVENTORY_COST
            ),
        ),
        AccountingRuleLine(
            line_no=2,
            account_id=2,
            side=AccountingRuleSide.CREDIT,
            amount_source=(
                AccountingAmountSource.INVENTORY_COST
            ),
        ),
    ]

    db = FakeSession(
        execute_values=[
            SimpleNamespace(
                id=1,
            ),
            existing,
        ]
    )

    result = (
        await seed_company_default_accounting_rules(
            session=db,
            company_id=1,
        )
    )

    assert result == (
        existing,
    )

    assert db.added == []

    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_seed_rejects_incompatible_existing_rule(
    monkeypatch,
):
    import app.services.company_default_accounting_rules_service as service

    async def fake_resolve(
        session,
        *,
        company_id,
        roles,
    ):
        return make_role_accounts()

    monkeypatch.setattr(
        service,
        "resolve_company_account_roles",
        fake_resolve,
    )

    existing = AccountingRule(
        id=55,
        company_id=1,
        code=SALES_FULFILLMENT_RULE_CODE,
        name="Unsafe",
        document_type=DocumentType.ISSUE,
        is_active=True,
    )

    existing.lines = [
        AccountingRuleLine(
            line_no=1,
            account_id=7,
            side=AccountingRuleSide.DEBIT,
            amount_source=(
                AccountingAmountSource.LINE_TOTAL
            ),
        ),
        AccountingRuleLine(
            line_no=2,
            account_id=9,
            side=AccountingRuleSide.CREDIT,
            amount_source=(
                AccountingAmountSource.LINE_TOTAL
            ),
        ),
    ]

    db = FakeSession(
        execute_values=[
            SimpleNamespace(
                id=1,
            ),
            existing,
        ]
    )

    with pytest.raises(
        CompanyDefaultAccountingRulesConflictError
    ):
        await seed_company_default_accounting_rules(
            session=db,
            company_id=1,
        )

    assert db.added == []

    assert db.flush_count == 0
