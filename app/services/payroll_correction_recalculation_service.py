"""Atomic, restricted payroll correction recalculation.

Supported correction:
    minimum_wage_context with a validated documentary reference.

The original payroll input and calculation remain unchanged.

This service does not commit, execute payroll payouts,
authorize deductions, or create accounting journal entries.

The caller must commit the completed transaction or roll it back.
"""

from __future__ import annotations

from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_correction_basis import verify_basis, replay_basis
from app.services.payroll_minimum_wage_service import MinimumWageError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
)

from app.models.payroll_correction import PayrollCorrection

from app.services.payroll_correction_preflight_service import (
    prepare_payroll_correction,
)

from app.services.payroll_correction_source_evidence_service import (
    capture_payroll_correction_source,
)

from app.services.payroll_correction_recalculation_contract import (
    verify_minimum_wage_correction_request,
    PayrollCorrectionRecalculationError,
)

from app.services.payroll_correction_service import (
    create_payroll_correction_link,
)


@serialized_payroll_mutation(PayrollCorrectionRecalculationError)
async def recalculate_minimum_wage_correction(
    db: AsyncSession,
    *,
    company_id: int,
    original_payroll_calculation_id: int,
    calculated_by: int,
    request_key: str,
    reason: str,
    corrected_minimum_wage_context: dict,
    expected_source_fingerprint_sha256: str,
) -> tuple[PayrollCalculation, PayrollCorrection]:
    """Calculate the next revision with append-only correction history.

    Calling code owns the surrounding transaction.

    Preconditions are checked before insertion and repeated
    immediately before creation. Never use this function
    to update a finalized PayrollInput.
    """

    if not isinstance(
        expected_source_fingerprint_sha256, str
    ) or len(expected_source_fingerprint_sha256) != 64:
        raise PayrollCorrectionRecalculationError(
            "A valid expected source SHA256 is required"
        )

    if (
        not isinstance(calculated_by, int)
        or isinstance(calculated_by, bool)
        or calculated_by <= 0
    ):
        raise PayrollCorrectionRecalculationError(
            "Valid calculated_by is required"
        )

    original = await db.scalar(
        select(PayrollCalculation)
        .where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id
            == original_payroll_calculation_id,
        )
        .with_for_update()
    )

    if original is None:
        raise PayrollCorrectionRecalculationError(
            "Original payroll calculation not found"
        )

    request = verify_minimum_wage_correction_request(
        company_id=company_id,
        original_calculation_id=original.id,
        request_key=request_key,
        reason=reason,
        existing_minimum_wage_assessment=(
            original.minimum_wage_assessment
        ),
        corrected_minimum_wage_context=(
            corrected_minimum_wage_context
        ),
    )

    existing = await db.scalar(
        select(PayrollCorrection).where(
            PayrollCorrection.company_id == company_id,
            PayrollCorrection.request_key == request.request_key,
        )
    )

    if existing is not None:
        replacement = await db.scalar(
            select(PayrollCalculation).where(
                PayrollCalculation.company_id == company_id,
                PayrollCalculation.id
                == existing.replacement_payroll_calculation_id,
            )
        )

        if (
            existing.original_payroll_calculation_id
            != original.id
            or existing.reason != request.reason
            or replacement is None
            or (replacement.minimum_wage_assessment or {}).get("correction_basis", {}).get("parent_source_fingerprint") != expected_source_fingerprint_sha256
            or (
                replacement.minimum_wage_assessment or {}
            ).get("request")
            != request.minimum_wage_context
        ):
            raise PayrollCorrectionRecalculationError(
                "Request key was used for different correction data"
            )

        return replacement, existing

    preflight = await prepare_payroll_correction(
        db,
        company_id=company_id,
        original_payroll_calculation_id=original.id,
        request_key=request.request_key,
        reason=request.reason,
    )

    evidence = await capture_payroll_correction_source(
        db,
        company_id=company_id,
        original_payroll_calculation_id=original.id,
        request_key=request.request_key,
        reason=request.reason,
    )

    if (
        evidence["source_fingerprint_sha256"]
        != expected_source_fingerprint_sha256
    ):
        raise PayrollCorrectionRecalculationError(
            "Payroll correction source changed since verification"
        )

    if evidence.get("source_evidence_complete") is not True:
        raise PayrollCorrectionRecalculationError(
            "Payroll source evidence is incomplete; "
            "correction revision creation is forbidden"
        )

    if evidence["automatic_revision_creation_allowed"]:
        raise PayrollCorrectionRecalculationError(
            "Unexpected source evidence authorization state"
        )

    original_lines = (await db.scalars(select(PayrollCalculationLine).where(
        PayrollCalculationLine.company_id == company_id,
        PayrollCalculationLine.payroll_calculation_id == original.id,
    ).order_by(PayrollCalculationLine.line_no, PayrollCalculationLine.id))).all()
    try:
        basis = verify_basis(original, original_lines)
        gross_amount, derived_lines, minimum_assessment = replay_basis(basis, request.minimum_wage_context)
    except MinimumWageError as exc:
        raise PayrollCorrectionRecalculationError(str(exc)) from exc
    # Preserve the frozen original earnings; only the documented context changes.
    basis.pop("seal", None)
    basis["parent_source_fingerprint"] = expected_source_fingerprint_sha256
    minimum_assessment["correction_basis"] = basis
    currency_code = original.currency_code

    before_write = await capture_payroll_correction_source(
        db,
        company_id=company_id,
        original_payroll_calculation_id=original.id,
        request_key=request.request_key,
        reason=request.reason,
    )

    if (
        before_write["source_fingerprint_sha256"]
        != expected_source_fingerprint_sha256
        or before_write["next_revision"]
        != preflight["next_revision"]
    ):
        raise PayrollCorrectionRecalculationError(
            "Payroll source or revision changed during recalculation"
        )

    async with db.begin_nested():
        replacement = PayrollCalculation(
            company_id=company_id,
            payroll_period_id=original.payroll_period_id,
            payroll_input_id=original.payroll_input_id,
            employment_contract_id=original.employment_contract_id,
            revision=preflight["next_revision"],
            currency_code=currency_code,
            gross_amount=gross_amount,
            calculated_by=calculated_by,
            minimum_wage_assessment=minimum_assessment,
        )

        db.add(replacement)
        await db.flush()

        for item in derived_lines:
            db.add(
                PayrollCalculationLine(
                    company_id=company_id,
                    payroll_calculation_id=replacement.id,
                    **item,
                )
            )

        await db.flush()

        correction = await create_payroll_correction_link(
            db,
            company_id=company_id,
            original_payroll_calculation_id=original.id,
            replacement_payroll_calculation_id=replacement.id,
            request_key=request.request_key,
            reason=request.reason,
            created_by=calculated_by,
        )

        await db.refresh(replacement)
        return replacement, correction
