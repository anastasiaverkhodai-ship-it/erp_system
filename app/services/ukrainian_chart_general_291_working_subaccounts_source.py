from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.ukrainian_working_subaccount_source_definition import (
    UkrainianWorkingSubaccountSource,
)


TEMPLATE_TYPE = (
    ChartOfAccountsTemplateType.GENERAL_291
)


GENERAL_291_WORKING_SUBACCOUNTS_V1 = (
    UkrainianWorkingSubaccountSource(
        code="281",
        name="Товари на складі",
        parent_code="28",
    ),
    UkrainianWorkingSubaccountSource(
        code="311",
        name="Поточні рахунки в національній валюті",
        parent_code="31",
    ),
    UkrainianWorkingSubaccountSource(
        code="361",
        name="Розрахунки з вітчизняними покупцями",
        parent_code="36",
    ),
    UkrainianWorkingSubaccountSource(
        code="371",
        name="Розрахунки за виданими авансами",
        parent_code="37",
    ),
    UkrainianWorkingSubaccountSource(
        code="631",
        name="Розрахунки з вітчизняними постачальниками",
        parent_code="63",
    ),
    UkrainianWorkingSubaccountSource(
        code="641",
        name="Розрахунки за податками",
        parent_code="64",
    ),
    UkrainianWorkingSubaccountSource(
        code="643",
        name="Податкові зобов'язання",
        parent_code="64",
    ),
    UkrainianWorkingSubaccountSource(
        code="644",
        name="Податковий кредит",
        parent_code="64",
    ),
    UkrainianWorkingSubaccountSource(
        code="681",
        name="Розрахунки за авансами одержаними",
        parent_code="68",
    ),
    UkrainianWorkingSubaccountSource(
        code="702",
        name="Дохід від реалізації товарів",
        parent_code="70",
    ),
    UkrainianWorkingSubaccountSource(
        code="704",
        name="Вирахування з доходу",
        parent_code="70",
    ),
    UkrainianWorkingSubaccountSource(
        code="902",
        name="Собівартість реалізованих товарів",
        parent_code="90",
    ),
)
