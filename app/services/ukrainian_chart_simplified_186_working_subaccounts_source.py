from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.ukrainian_working_subaccount_source_definition import (
    UkrainianWorkingSubaccountSource,
)


TEMPLATE_TYPE = (
    ChartOfAccountsTemplateType.SIMPLIFIED_186
)


# Intentionally empty.
#
# GENERAL_291 working subaccounts must never be copied
# automatically into SIMPLIFIED_186.
#
# The simplified chart will use its own ERP working-account
# profile where additional operational detail is required.
SIMPLIFIED_186_WORKING_SUBACCOUNTS_V1: tuple[
    UkrainianWorkingSubaccountSource,
    ...,
] = ()
