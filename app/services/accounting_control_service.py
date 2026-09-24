from datetime import date

from app.services.ap_inventory_gl_control_service import reconcile_ap_inventory_gl

from app.services.cash_bank_gl_control_service import reconcile_cash_bank_gl

from app.services.ar_gl_control_service import reconcile_ar_gl

from app.services.input_vat_gl_reconciliation_service import reconcile_input_vat_gl

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.accounting_controls import (
    AccountingControlFamilyResult,
    ConsolidatedAccountingControlReport,
)
from app.services.output_vat_gl_reconciliation_service import (
    reconcile_output_vat_gl,
)


async def get_consolidated_accounting_controls(
    session: AsyncSession,
    *,
    company_id: int,
    date_from: date,
    date_to: date,
) -> ConsolidatedAccountingControlReport:
    """
    Read-only consolidated accounting control surface.

    Only families with a verified canonical domain source and a verified
    source-to-GL comparator may be reported as implemented.

    Missing comparators are explicit NOT_IMPLEMENTED results. They must
    never be synthesized from unrelated operational reconciliation
    services and this report never repairs accounting state.
    """
    if date_from > date_to:
        raise ValueError(
            "date_from must be less than or equal to date_to"
        )

    output_vat = await reconcile_output_vat_gl(
        session,
        company_id=company_id,
        date_from=date_from,
        date_to=date_to,
    )

    input_vat = await reconcile_input_vat_gl(
        session, company_id=company_id, date_from=date_from, date_to=date_to,
    )

    ar = await reconcile_ar_gl(
        session, company_id=company_id, date_from=date_from, date_to=date_to,
    )

    cash_bank = await reconcile_cash_bank_gl(
        session, company_id=company_id, date_from=date_from, date_to=date_to,
    )

    purchase = {}
    for family in ("ap", "inventory"):
        purchase[family] = await reconcile_ap_inventory_gl(
            session, company_id=company_id, date_from=date_from, date_to=date_to, family=family,
        )

    def purchase_family(family):
        result = purchase[family]
        return AccountingControlFamilyResult(
            family=family, status="matched" if result.matched else "mismatch",
            expected_amount=result.expected_amount, posted_amount=result.posted_amount,
            difference=result.difference,
            issue_count=sum(len(source.issues) for source in result.sources) + int(result.difference != 0) + len(result.unattributed_journal_ids),
            note="Economic balance at date_to and source journal checks; commercial quantities/obligations are shown separately.",
            details=result.model_dump(mode="json"),
        )

    families = [
        AccountingControlFamilyResult(
            family="ar", status="matched" if ar.matched else "mismatch",
            expected_amount=ar.bridge.economic_balance,
            posted_amount=ar.bridge.posted_balance,
            difference=ar.bridge.gl_difference,
            issue_count=sum(len(source.issues) for source in ar.sources) + int(ar.bridge.gl_difference != 0) + len(ar.unattributed_journal_ids),
            note="AR balance at date_to plus source journal checks in the requested period; commercial bridge is explanatory.",
            details=ar.model_dump(mode="json"),
        ),
        purchase_family("ap"),
        AccountingControlFamilyResult(
            family="cash_bank", status="matched" if cash_bank.matched else "mismatch",
            expected_amount=cash_bank.expected_amount, posted_amount=cash_bank.posted_amount,
            difference=cash_bank.difference,
            issue_count=sum(len(source.issues) for source in cash_bank.sources) + len(cash_bank.unattributed_journal_ids),
            note="Cash/bank payment turnover and cancellations; unexplained non-opening GL movements are reported.",
            details=cash_bank.model_dump(mode="json"),
        ),
        purchase_family("inventory"),
        AccountingControlFamilyResult(
            family="input_vat",
            status="matched" if input_vat.matched else "mismatch",
            expected_amount=input_vat.expected_input_vat,
            posted_amount=input_vat.posted_input_vat,
            difference=input_vat.difference,
            issue_count=len(input_vat.issues),
            note="Legal INPUT VAT recognition events versus their GL journals; excludes other 641/644 movements.",
            details=input_vat.model_dump(mode="json"),
        ),
        AccountingControlFamilyResult(
            family="output_vat",
            status=(
                "matched"
                if output_vat.matched
                else "mismatch"
            ),
            expected_amount=output_vat.expected_output_vat,
            posted_amount=output_vat.posted_output_vat,
            difference=output_vat.difference,
            issue_count=len(output_vat.issues),
            note="Verified OUTPUT VAT source-to-GL control.",
        ),
    ]

    implemented = [
        family
        for family in families
        if family.status != "not_implemented"
    ]

    coverage_complete = len(implemented) == len(families)
    checked_families_matched = bool(implemented) and all(
        family.status == "matched" for family in implemented
    )
    has_mismatch = any(family.status == "mismatch" for family in implemented)

    return ConsolidatedAccountingControlReport(
        company_id=company_id,
        date_from=date_from,
        date_to=date_to,
        matched=coverage_complete and checked_families_matched,
        status="mismatch" if has_mismatch else ("matched" if coverage_complete else "incomplete"),
        coverage_complete=coverage_complete,
        checked_families_matched=checked_families_matched,
        implemented_family_count=len(implemented),
        not_implemented_family_count=(
            len(families) - len(implemented)
        ),
        families=families,
    )
