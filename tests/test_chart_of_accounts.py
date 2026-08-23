import inspect

from sqlalchemy import CheckConstraint

from app.models.company import Company
from app.schemas.company import CompanyCreate
from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)
from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.company_chart_of_accounts_backfill_service import (
    CompanyChartOfAccountsBackfillError,
    CompanyChartOfAccountsBackfillResult,
    backfill_company_chart_of_accounts,
)
from app.services.company_chart_of_accounts_seeding_service import (
    CompanyChartOfAccountsConflictError,
    CompanyChartOfAccountsMismatchError,
    CompanyChartOfAccountsSeedingError,
    seed_company_chart_of_accounts,
)
from app.services.ukrainian_chart_source_registry import (
    get_ukrainian_synthetic_account_source,
)
from app.services.ukrainian_chart_synthetic_metadata import (
    get_ukrainian_synthetic_account_metadata,
)
from app.services.ukrainian_system_account_catalog_builder import (
    build_ukrainian_system_account_catalog,
)


def test_chart_template_values() -> None:
    assert (
        ChartOfAccountsTemplateType.GENERAL_291.value
        == "general_291"
    )
    assert (
        ChartOfAccountsTemplateType.SIMPLIFIED_186.value
        == "simplified_186"
    )


def test_general_291_source() -> None:
    source = get_ukrainian_synthetic_account_source(
        ChartOfAccountsTemplateType.GENERAL_291
    )

    codes = [
        account.code
        for account in source
    ]

    assert len(codes) == 84
    assert len(codes) == len(set(codes))
    assert all(
        code.isdigit() and len(code) == 2
        for code in codes
    )

    assert all(
        code in codes
        for code in (
            "01",
            "09",
            "10",
            "28",
            "70",
            "79",
            "84",
            "90",
            "98",
        )
    )

    assert "75" not in codes
    assert "85" not in codes
    assert "99" not in codes


def test_simplified_186_source() -> None:
    source = get_ukrainian_synthetic_account_source(
        ChartOfAccountsTemplateType.SIMPLIFIED_186
    )

    codes = [
        account.code
        for account in source
    ]

    assert len(codes) == 30
    assert len(codes) == len(set(codes))
    assert all(
        code.isdigit() and len(code) == 2
        for code in codes
    )

    assert "10" in codes
    assert "70" in codes
    assert "79" in codes
    assert "90" in codes
    assert "91" in codes
    assert "97" in codes

    assert "84" not in codes
    assert "85" not in codes
    assert "92" not in codes
    assert "96" not in codes

    assert not any(
        code.startswith("0")
        for code in codes
    )


def test_general_291_catalog() -> None:
    catalog = build_ukrainian_system_account_catalog(
        ChartOfAccountsTemplateType.GENERAL_291
    )

    assert catalog.count == 84

    ordered = catalog.seed_order()

    assert ordered[0].code == "01"
    assert ordered[-1].code == "98"

    assert len(catalog.roots()) == 84

    assert all(
        account.is_postable
        for account in catalog.all()
    )


def test_simplified_186_catalog() -> None:
    catalog = build_ukrainian_system_account_catalog(
        ChartOfAccountsTemplateType.SIMPLIFIED_186
    )

    assert catalog.count == 30

    ordered = catalog.seed_order()

    assert ordered[0].code == "10"
    assert ordered[-1].code == "97"

    assert catalog.get_or_none("92") is None
    assert catalog.get_or_none("97") is not None


def test_general_291_special_metadata() -> None:
    account_13 = (
        get_ukrainian_synthetic_account_metadata(
            template_type=(
                ChartOfAccountsTemplateType.GENERAL_291
            ),
            code="13",
        )
    )

    account_38 = (
        get_ukrainian_synthetic_account_metadata(
            template_type=(
                ChartOfAccountsTemplateType.GENERAL_291
            ),
            code="38",
        )
    )

    account_45 = (
        get_ukrainian_synthetic_account_metadata(
            template_type=(
                ChartOfAccountsTemplateType.GENERAL_291
            ),
            code="45",
        )
    )

    account_79 = (
        get_ukrainian_synthetic_account_metadata(
            template_type=(
                ChartOfAccountsTemplateType.GENERAL_291
            ),
            code="79",
        )
    )

    assert account_13.account_type == AccountType.ASSET
    assert (
        account_13.normal_balance
        == AccountNormalBalance.CREDIT
    )

    assert account_38.account_type == AccountType.ASSET
    assert (
        account_38.normal_balance
        == AccountNormalBalance.CREDIT
    )

    assert account_45.account_type == AccountType.EQUITY
    assert (
        account_45.normal_balance
        == AccountNormalBalance.DEBIT
    )

    assert account_79.account_type == AccountType.EQUITY
    assert (
        account_79.normal_balance
        == AccountNormalBalance.DEBIT_CREDIT
    )


def test_simplified_186_special_metadata() -> None:
    account_13 = (
        get_ukrainian_synthetic_account_metadata(
            template_type=(
                ChartOfAccountsTemplateType.SIMPLIFIED_186
            ),
            code="13",
        )
    )

    account_40 = (
        get_ukrainian_synthetic_account_metadata(
            template_type=(
                ChartOfAccountsTemplateType.SIMPLIFIED_186
            ),
            code="40",
        )
    )

    account_79 = (
        get_ukrainian_synthetic_account_metadata(
            template_type=(
                ChartOfAccountsTemplateType.SIMPLIFIED_186
            ),
            code="79",
        )
    )

    account_97 = (
        get_ukrainian_synthetic_account_metadata(
            template_type=(
                ChartOfAccountsTemplateType.SIMPLIFIED_186
            ),
            code="97",
        )
    )

    assert account_13.account_type == AccountType.ASSET
    assert (
        account_13.normal_balance
        == AccountNormalBalance.CREDIT
    )

    assert account_40.account_type == AccountType.EQUITY
    assert (
        account_40.normal_balance
        == AccountNormalBalance.DEBIT_CREDIT
    )

    assert account_79.account_type == AccountType.EQUITY
    assert (
        account_79.normal_balance
        == AccountNormalBalance.DEBIT_CREDIT
    )

    assert account_97.account_type == AccountType.EXPENSE
    assert (
        account_97.normal_balance
        == AccountNormalBalance.DEBIT
    )


def test_company_create_chart_template() -> None:
    general = CompanyCreate(
        name="General Test Company"
    )

    simplified = CompanyCreate(
        name="Simplified Test Company",
        chart_of_accounts_template=(
            ChartOfAccountsTemplateType.SIMPLIFIED_186
        ),
    )

    assert (
        general.chart_of_accounts_template
        == ChartOfAccountsTemplateType.GENERAL_291
    )

    assert (
        simplified.chart_of_accounts_template
        == ChartOfAccountsTemplateType.SIMPLIFIED_186
    )


def test_company_chart_template_check_constraint() -> None:
    checks = [
        constraint
        for constraint in Company.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    ]

    chart_check = next(
        (
            constraint
            for constraint in checks
            if constraint.name
            == "chart_of_accounts_template_enum"
        ),
        None,
    )

    assert chart_check is not None

    sql = str(chart_check.sqltext)

    assert "general_291" in sql
    assert "simplified_186" in sql


def test_seeding_service_contract() -> None:
    assert inspect.iscoroutinefunction(
        seed_company_chart_of_accounts
    )

    assert issubclass(
        CompanyChartOfAccountsConflictError,
        CompanyChartOfAccountsSeedingError,
    )

    assert issubclass(
        CompanyChartOfAccountsMismatchError,
        CompanyChartOfAccountsSeedingError,
    )


def test_backfill_service_contract() -> None:
    assert inspect.iscoroutinefunction(
        backfill_company_chart_of_accounts
    )

    assert issubclass(
        CompanyChartOfAccountsBackfillError,
        Exception,
    )

    result = CompanyChartOfAccountsBackfillResult(
        company_id=1,
        template_type=(
            ChartOfAccountsTemplateType.GENERAL_291
        ),
        promoted_count=5,
        created_count=79,
        custom_count=5,
    )

    assert result.company_id == 1
    assert result.promoted_count == 5
    assert result.created_count == 79
    assert result.custom_count == 5
