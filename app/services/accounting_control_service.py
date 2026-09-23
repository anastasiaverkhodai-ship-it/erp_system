from datetime import date

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

    families = [
        AccountingControlFamilyResult(
            family="ar",
            status="not_implemented",
            note=(
                "Canonical AR open-item source exists, but a verified "
                "AR source-to-GL comparator is not implemented."
            ),
        ),
        AccountingControlFamilyResult(
            family="ap",
            status="not_implemented",
            note=(
                "Canonical AP open-item source exists, but a verified "
                "AP source-to-GL comparator is not implemented."
            ),
        ),
        AccountingControlFamilyResult(
            family="cash_bank",
            status="not_implemented",
            note=(
                "No verified canonical cash/bank source-to-GL "
                "comparator is implemented."
            ),
        ),
        AccountingControlFamilyResult(
            family="inventory",
            status="not_implemented",
            note=(
                "No verified canonical inventory value-to-GL "
                "comparator is implemented."
            ),
        ),
        AccountingControlFamilyResult(
            family="input_vat",
            status="not_implemented",
            note=(
                "INPUT VAT journal lifecycle exists, but a dedicated "
                "read-only INPUT VAT source-to-GL comparator is not "
                "implemented."
            ),
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
