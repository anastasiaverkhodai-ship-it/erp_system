from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accounting_rule import AccountingRule
from app.models.accounting_rule_line import (
    AccountingAmountSource,
    AccountingRuleLine,
    AccountingRuleSide,
)
from app.models.company import Company
from app.models.document import DocumentType
from app.services.accounting_account_role_resolver import (
    AccountingAccountRoleResolutionError,
    resolve_company_account_roles,
)
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.sales_fulfillment_accounting_rule import (
    SalesFulfillmentAccountingRuleValidationError,
    validate_sales_fulfillment_accounting_rule,
)


SALES_FULFILLMENT_RULE_CODE = (
    "SALES_FULFILLMENT_ISSUE"
)

SALES_FULFILLMENT_RULE_NAME = (
    "Відпуск товарів покупцю: собівартість"
)


class CompanyDefaultAccountingRulesError(
    Exception
):
    """Base error for company default accounting rules."""


class CompanyDefaultAccountingRulesCompanyNotFoundError(
    CompanyDefaultAccountingRulesError
):
    """Company does not exist."""


class CompanyDefaultAccountingRulesRoleError(
    CompanyDefaultAccountingRulesError
):
    """Required accounting roles cannot be resolved."""


class CompanyDefaultAccountingRulesConflictError(
    CompanyDefaultAccountingRulesError
):
    """Existing default rule has incompatible semantics."""


async def seed_company_default_accounting_rules(
    *,
    session: AsyncSession,
    company_id: int,
) -> tuple[
    AccountingRule,
    ...,
]:
    """
    Seed default company accounting rules.

    Current default:
      SALES_FULFILLMENT_ISSUE
        Dr GOODS_COGS / INVENTORY_COST
        Cr INVENTORY_GOODS / INVENTORY_COST

    The operation is:
      - company-scoped;
      - template-aware through accounting roles;
      - idempotent;
      - non-committing.

    Caller owns COMMIT / ROLLBACK.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be positive"
        )

    company = (
        await session.execute(
            select(Company)
            .where(
                Company.id == company_id
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if company is None:
        raise (
            CompanyDefaultAccountingRulesCompanyNotFoundError(
                "Company not found"
            )
        )

    try:
        accounts = (
            await resolve_company_account_roles(
                session,
                company_id=company_id,
                roles=(
                    AccountingAccountRole.GOODS_COGS,
                    AccountingAccountRole.INVENTORY_GOODS,
                ),
            )
        )
    except AccountingAccountRoleResolutionError as exc:
        raise CompanyDefaultAccountingRulesRoleError(
            "Default Sales fulfillment accounting "
            f"roles cannot be resolved: {exc}"
        ) from exc

    cogs_account = accounts[
        AccountingAccountRole.GOODS_COGS
    ]

    inventory_account = accounts[
        AccountingAccountRole.INVENTORY_GOODS
    ]

    existing = (
        await session.execute(
            select(AccountingRule)
            .options(
                selectinload(
                    AccountingRule.lines
                )
            )
            .where(
                AccountingRule.company_id
                == company_id,
                AccountingRule.code
                == SALES_FULFILLMENT_RULE_CODE,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        try:
            validate_sales_fulfillment_accounting_rule(
                accounting_rule=existing,
                cogs_account_id=cogs_account.id,
                inventory_account_id=(
                    inventory_account.id
                ),
            )
        except (
            SalesFulfillmentAccountingRuleValidationError
        ) as exc:
            raise (
                CompanyDefaultAccountingRulesConflictError(
                    "Existing default Sales fulfillment "
                    "accounting rule has incompatible "
                    f"semantics: {exc}"
                )
            ) from exc

        return (
            existing,
        )

    rule = AccountingRule(
        company_id=company_id,
        code=SALES_FULFILLMENT_RULE_CODE,
        name=SALES_FULFILLMENT_RULE_NAME,
        document_type=DocumentType.ISSUE,
        is_active=True,
    )

    rule.lines = [
        AccountingRuleLine(
            line_no=1,
            account_id=cogs_account.id,
            side=AccountingRuleSide.DEBIT,
            amount_source=(
                AccountingAmountSource.INVENTORY_COST
            ),
            description=(
                "Собівартість реалізованих товарів"
            ),
        ),
        AccountingRuleLine(
            line_no=2,
            account_id=inventory_account.id,
            side=AccountingRuleSide.CREDIT,
            amount_source=(
                AccountingAmountSource.INVENTORY_COST
            ),
            description=(
                "Списання товарів зі складу"
            ),
        ),
    ]

    session.add(
        rule
    )

    await session.flush()

    return (
        rule,
    )
