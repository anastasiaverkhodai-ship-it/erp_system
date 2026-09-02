from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_credit_evidence import (
    TaxCreditEvidence,
)
from app.services.input_tax_recognition_calculation_service import (
    InputTaxRecognitionCalculationError,
)
from app.services.input_tax_recognition_candidate_loader_service import (
    InputTaxRecognitionCandidateLoaderError,
)
from app.services.input_tax_recognition_evidence_allocation_service import (
    InputTaxRecognitionEvidenceAllocationError,
)
from app.services.input_tax_recognition_persistence_service import (
    InputTaxRecognitionPersistenceError,
)
from app.services.input_tax_recognition_reconciliation_service import (
    InputTaxRecognitionReconciliationError,
    InputTaxRecognitionReconciliationResult,
    reconcile_input_tax_calculation_from_active_sources,
)
from app.services.tax_credit_evidence_persistence_service import (
    create_tax_credit_evidence,
    reverse_tax_credit_evidence,
)
from app.services.tax_credit_evidence_types import (
    TaxCreditEvidenceType,
)


class TaxCreditEvidenceLifecycleError(
    Exception
):
    """
    Tax-credit evidence persisted successfully in the current
    transaction but INPUT VAT reconciliation failed.

    Caller owns COMMIT / ROLLBACK, so the entire mutation remains
    atomic when the caller rolls the transaction back.
    """


@dataclass(
    frozen=True,
    slots=True,
)
class TaxCreditEvidenceLifecycleResult:
    evidence: TaxCreditEvidence
    recognition: (
        InputTaxRecognitionReconciliationResult
    )


_INPUT_RECOGNITION_ERRORS = (
    InputTaxRecognitionCalculationError,
    InputTaxRecognitionCandidateLoaderError,
    InputTaxRecognitionEvidenceAllocationError,
    InputTaxRecognitionPersistenceError,
    InputTaxRecognitionReconciliationError,
)


def _validate_adjustment_date(
    adjustment_date: date,
) -> None:
    if not isinstance(
        adjustment_date,
        date,
    ):
        raise ValueError(
            "adjustment_date must be a date"
        )


async def _reconcile_after_evidence_mutation(
    db: AsyncSession,
    *,
    evidence: TaxCreditEvidence,
    adjustment_date: date,
    created_by: int,
) -> InputTaxRecognitionReconciliationResult:
    """
    Recalculate one INPUT VAT TaxCalculation after immutable legal
    evidence capacity changes.
    """

    try:
        return (
            await reconcile_input_tax_calculation_from_active_sources(
                db,
                company_id=evidence.company_id,
                tax_calculation_id=(
                    evidence.tax_calculation_id
                ),
                adjustment_date=(
                    adjustment_date
                ),
                created_by=created_by,
            )
        )

    except _INPUT_RECOGNITION_ERRORS as exc:
        raise TaxCreditEvidenceLifecycleError(
            "INPUT VAT recognition reconciliation "
            "failed after TaxCreditEvidence mutation: "
            f"{exc}"
        ) from exc


async def create_tax_credit_evidence_and_reconcile(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    evidence_type: TaxCreditEvidenceType,
    evidence_number: str,
    evidence_date: date,
    credit_available_date: date,
    evidenced_taxable_base: Decimal,
    evidenced_tax_amount: Decimal,
    currency_code: str,
    adjustment_date: date,
    created_by: int,
) -> TaxCreditEvidenceLifecycleResult:
    """
    Persist immutable INPUT tax-credit evidence and immediately
    reconcile the affected TaxCalculation in the same transaction.

    adjustment_date is the business as-of date of this lifecycle
    mutation. It is deliberately separate from evidence_date and
    credit_available_date so back-entered legal evidence can
    reconcile all economic sources that already exist.

    Caller owns COMMIT / ROLLBACK.
    """

    _validate_adjustment_date(
        adjustment_date
    )

    evidence = (
        await create_tax_credit_evidence(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
            evidence_type=evidence_type,
            evidence_number=(
                evidence_number
            ),
            evidence_date=evidence_date,
            credit_available_date=(
                credit_available_date
            ),
            evidenced_taxable_base=(
                evidenced_taxable_base
            ),
            evidenced_tax_amount=(
                evidenced_tax_amount
            ),
            currency_code=currency_code,
            created_by=created_by,
        )
    )

    recognition = (
        await _reconcile_after_evidence_mutation(
            db,
            evidence=evidence,
            adjustment_date=(
                adjustment_date
            ),
            created_by=created_by,
        )
    )

    return TaxCreditEvidenceLifecycleResult(
        evidence=evidence,
        recognition=recognition,
    )


async def reverse_tax_credit_evidence_and_reconcile(
    db: AsyncSession,
    *,
    company_id: int,
    evidence_id: int,
    reversal_date: date,
    reversed_by: int,
) -> TaxCreditEvidenceLifecycleResult:
    """
    Persist an immutable TaxCreditEvidence reversal and immediately
    reconcile INPUT VAT in the same transaction.

    Evidence reversal effective_date and INPUT reconciliation
    adjustment_date are the same reversal_date.

    Caller owns COMMIT / ROLLBACK.
    """

    if not isinstance(
        reversal_date,
        date,
    ):
        raise ValueError(
            "reversal_date must be a date"
        )

    reversal = (
        await reverse_tax_credit_evidence(
            db,
            company_id=company_id,
            tax_credit_evidence_id=(
                evidence_id
            ),
            reversal_date=reversal_date,
            reversed_by=(
                reversed_by
            ),
        )
    )

    recognition = (
        await _reconcile_after_evidence_mutation(
            db,
            evidence=reversal,
            adjustment_date=(
                reversal_date
            ),
            created_by=reversed_by,
        )
    )

    return TaxCreditEvidenceLifecycleResult(
        evidence=reversal,
        recognition=recognition,
    )
