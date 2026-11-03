"""Verify corrected severance calculation and tax evidence.

No journal posting, payment, original-payroll mutation, or statutory
approval is performed here. A proposal cannot supply its own tax truth.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import PayrollCalculation
from app.models.payroll import PayrollCalculationLine
from app.models.payroll_severance_correction_case import (
    PayrollSeveranceCorrectionCase,
)
from app.models.payroll_severance_financial_proposal import (
    PayrollSeveranceFinancialProposal,
)
from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)
from app.services.payroll_severance_financial_approval_gate import (
    snapshot_from_json,
)
from app.services.payroll_severance_net_tax_settlement import (
    SeveranceFinancialSnapshot,
)


class CorrectedSeveranceTaxEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class CorrectedSeveranceTaxEvidence:
    company_id: int
    financial_proposal_id: int
    original_calculation_id: int
    corrected_calculation_id: int
    corrected_source_id: int
    corrected_gross: Decimal
    personal_income_tax: Decimal
    military_levy: Decimal
    employer_contribution: Decimal
    net: Decimal
    source_reconciled: bool
    tax_reconciled: bool
    posting_authorized: bool = False
    payment_authorized: bool = False


def verify_corrected_tax_amounts(
    *,
    proposal,
    corrected_financial,
    corrected_gross,
):
    """Check every financial component against a calculated result."""
    if not isinstance(corrected_financial, SeveranceFinancialSnapshot):
        raise CorrectedSeveranceTaxEvidenceError(
            "Calculated severance-only financial snapshot required"
        )

    try:
        calculated = corrected_financial.validate()
        proposed = snapshot_from_json(
            proposal.proposed_financial
        ).validate()
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise CorrectedSeveranceTaxEvidenceError(
            "Invalid proposed or calculated tax snapshot"
        ) from exc

    try:
        gross = Decimal(str(corrected_gross))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise CorrectedSeveranceTaxEvidenceError(
            "Invalid corrected source gross"
        ) from exc

    if (
        not gross.is_finite()
        or gross <= 0
        or gross.as_tuple().exponent < -2
        or calculated["gross"] != gross
    ):
        raise CorrectedSeveranceTaxEvidenceError(
            "Calculated gross differs from corrected legal source"
        )

    for name in (
        "gross",
        "personal_income_tax",
        "military_levy",
        "employer_contribution",
        "net",
    ):
        if proposed[name] != calculated[name]:
            raise CorrectedSeveranceTaxEvidenceError(
                f"Corrected statutory amount mismatch: {name}"
            )

    return calculated


async def verify_corrected_severance_tax_evidence(
    db,
    *,
    company_id: int,
    financial_proposal_id: int,
    corrected_calculation_id: int,
) -> CorrectedSeveranceTaxEvidence:
    """Re-derive evidence from persisted company-scoped payroll sources.

    The corrected calculation ID must identify an existing independent
    calculation, not the already-posted historical calculation.

    Caller owns transaction. This function never commits or writes.
    """
    for name, value in (
        ("company_id", company_id),
        ("financial_proposal_id", financial_proposal_id),
        ("corrected_calculation_id", corrected_calculation_id),
    ):
        if type(value) is not int or value <= 0:
            raise CorrectedSeveranceTaxEvidenceError(
                f"Invalid {name}"
            )

    proposal = await db.scalar(
        select(PayrollSeveranceFinancialProposal).where(
            PayrollSeveranceFinancialProposal.company_id == company_id,
            PayrollSeveranceFinancialProposal.id
                == financial_proposal_id,
        )
    )
    if proposal is None or proposal.status != "review_required":
        raise CorrectedSeveranceTaxEvidenceError(
            "Immutable financial proposal required"
        )

    case = await db.scalar(
        select(PayrollSeveranceCorrectionCase).where(
            PayrollSeveranceCorrectionCase.company_id == company_id,
            PayrollSeveranceCorrectionCase.id
                == proposal.correction_case_id,
        )
    )
    if case is None or case.status != "review_required":
        raise CorrectedSeveranceTaxEvidenceError(
            "Original correction case required"
        )

    if corrected_calculation_id == case.original_payroll_calculation_id:
        raise CorrectedSeveranceTaxEvidenceError(
            "Historical original payroll cannot be corrected in place"
        )

    from app.services.payroll_severance_corrected_source_reconciliation import (
        CorrectedSeveranceSourceError,
        load_corrected_severance_source_reconciliation,
    )

    try:
        lineage = await load_corrected_severance_source_reconciliation(
            db,
            company_id=company_id,
            financial_proposal_id=financial_proposal_id,
        )
    except CorrectedSeveranceSourceError as exc:
        raise CorrectedSeveranceTaxEvidenceError(
            "Corrected source lineage not verified"
        ) from exc

    if (
        lineage.company_id != company_id
        or lineage.financial_proposal_id != financial_proposal_id
        or lineage.original_source_id
            != case.original_severance_source_id
        or lineage.source_chain_verified is not True
        or lineage.proposed_gross_reconciled is not True
        or lineage.posting_authorized is not False
    ):
        raise CorrectedSeveranceTaxEvidenceError(
            "Corrected source lineage identity mismatch"
        )

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == corrected_calculation_id,
            PayrollCalculation.payroll_input_id == case.payroll_input_id,
        )
    )
    if calculation is None:
        raise CorrectedSeveranceTaxEvidenceError(
            "Company-scoped corrected payroll calculation required"
        )

    severance_lines = list((
        await db.scalars(
            select(PayrollCalculationLine).where(
                PayrollCalculationLine.company_id == company_id,
                PayrollCalculationLine.payroll_calculation_id
                    == corrected_calculation_id,
                PayrollCalculationLine.line_type == "severance",
            )
        )
    ).all())

    if (
        len(severance_lines) != 1
        or severance_lines[0].source_termination_severance_id
            != lineage.corrected_source_id
    ):
        raise CorrectedSeveranceTaxEvidenceError(
            "Corrected calculation must use exactly the latest "
            "verified severance revision"
        )

    source = await db.scalar(
        select(PayrollTerminationSeverance).where(
            PayrollTerminationSeverance.company_id == company_id,
            PayrollTerminationSeverance.id
                == lineage.corrected_source_id,
        )
    )
    if source is None:
        raise CorrectedSeveranceTaxEvidenceError(
            "Corrected legal source not found"
        )

    # Independently re-derive and compare the persisted statutory
    # classification. This rejects superseded calculations.
    from app.services.payroll_severance_trusted_classification import (
        TRUSTED_POLICIES,
        require_trusted_severance_snapshot,
    )

    try:
        statutory = await require_trusted_severance_snapshot(
            db,
            company_id=company_id,
            payroll_calculation_id=corrected_calculation_id,
        )
    except ValueError as exc:
        raise CorrectedSeveranceTaxEvidenceError(
            "Current trusted corrected statutory snapshot required"
        ) from exc

    if (
        statutory.severance_source_ids
            != (lineage.corrected_source_id,)
        or statutory.severance_gross != lineage.corrected_gross
        or statutory.income_date != source.termination_date
    ):
        raise CorrectedSeveranceTaxEvidenceError(
            "Statutory snapshot does not match corrected revision"
        )

    from app.services.payroll_severance_verified_tax_bridge import (
        load_severance_only_financial_snapshot,
    )

    try:
        financial = await load_severance_only_financial_snapshot(
            db,
            company_id=company_id,
            calculation_id=corrected_calculation_id,
            income_date=source.termination_date,
            policies=TRUSTED_POLICIES,
        )
    except ValueError as exc:
        raise CorrectedSeveranceTaxEvidenceError(
            "Calculated severance-only tax evidence unavailable"
        ) from exc

    amounts = verify_corrected_tax_amounts(
        proposal=proposal,
        corrected_financial=financial,
        corrected_gross=lineage.corrected_gross,
    )

    return CorrectedSeveranceTaxEvidence(
        company_id=company_id,
        financial_proposal_id=financial_proposal_id,
        original_calculation_id=case.original_payroll_calculation_id,
        corrected_calculation_id=corrected_calculation_id,
        corrected_source_id=lineage.corrected_source_id,
        corrected_gross=amounts["gross"],
        personal_income_tax=amounts["personal_income_tax"],
        military_levy=amounts["military_levy"],
        employer_contribution=amounts["employer_contribution"],
        net=amounts["net"],
        source_reconciled=True,
        tax_reconciled=True,
    )
