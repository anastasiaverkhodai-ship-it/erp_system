"""Deterministic evidence for a payroll correction source.

This is a READ-ONLY evidence service, not a correction executor.

The fingerprint covers the current Payroll Input, salary slices,
calculation header and calculation lines.

Legacy fingerprints alone never authorize a correction. A database-sealed
creation-time basis supports only minimum-wage-context correction: frozen
other earnings remain unchanged. Taxes, posting and payouts stay separate.

No database writes or transaction commits are performed.
"""

from __future__ import annotations

import hashlib
import json

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInput,
    PayrollInputSalarySlice,
)

from app.services.payroll_correction_preflight_service import (
    prepare_payroll_correction,
    PayrollCorrectionPreflightConflict,
)
from app.services.payroll_mutation_guard import (
    serialized_payroll_mutation,
)


class PayrollCorrectionEvidenceError(Exception):
    pass


def _canonical_value(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value

    if isinstance(value, Decimal):
        if not value.is_finite():
            raise PayrollCorrectionEvidenceError(
                "Non-finite monetary source value"
            )
        return {"decimal": str(value)}

    if isinstance(value, (datetime, date)):
        return {
            "type": type(value).__name__,
            "value": value.isoformat(),
        }

    if isinstance(value, UUID):
        return {"uuid": str(value)}

    if isinstance(value, Enum):
        return _canonical_value(value.value)

    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]

    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise PayrollCorrectionEvidenceError(
                "Source dictionary keys must be strings"
            )
        return {
            key: _canonical_value(item)
            for key, item in sorted(value.items())
        }

    raise PayrollCorrectionEvidenceError(
        "Unsupported source value type: "
        + type(value).__name__
    )


def _model_snapshot(row):
    return {
        column.key: _canonical_value(
            getattr(row, column.key)
        )
        for column in row.__mapper__.columns
    }


def _fingerprint(snapshot):
    encoded = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


@serialized_payroll_mutation(PayrollCorrectionEvidenceError)
async def capture_payroll_correction_source(
    db: AsyncSession,
    *,
    company_id: int,
    original_payroll_calculation_id: int,
    request_key: str,
    reason: str,
) -> dict:
    """Capture evidence while holding the company mutation lock.

    This result is not a bearer authorization token. The correction executor
    revalidates it in its own locked transaction. Complete evidence is explicitly
    scoped to minimum_wage_context_only; it never authorizes posting or payment.
    """

    try:
        preflight = await prepare_payroll_correction(
            db,
            company_id=company_id,
            original_payroll_calculation_id=(
                original_payroll_calculation_id
            ),
            request_key=request_key,
            reason=reason,
        )
    except PayrollCorrectionPreflightConflict as exc:
        raise PayrollCorrectionEvidenceError(str(exc)) from exc

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id
            == original_payroll_calculation_id,
        )
    )

    if calculation is None:
        raise PayrollCorrectionEvidenceError(
            "Original payroll calculation not found"
        )

    if (calculation.minimum_wage_assessment or {}).get("correction_basis", {}).get("seal"):
        from app.services.payroll_correction_basis import verify_basis
        from app.services.payroll_minimum_wage_service import MinimumWageError
        lines = (await db.scalars(select(PayrollCalculationLine).where(
            PayrollCalculationLine.company_id == company_id,
            PayrollCalculationLine.payroll_calculation_id == calculation.id,
        ).order_by(PayrollCalculationLine.line_no, PayrollCalculationLine.id))).all()
        try:
            verify_basis(calculation, lines)
        except MinimumWageError as exc:
            raise PayrollCorrectionEvidenceError(str(exc)) from exc
        snapshot = {"schema": "payroll-minimum-context-evidence-v1",
                    "calculation": _model_snapshot(calculation),
                    "calculation_lines": [_model_snapshot(line) for line in lines]}
        return {**preflight, "source_fingerprint_sha256": _fingerprint(snapshot),
                "source_evidence_schema": snapshot["schema"],
                "source_evidence_complete": True,
                "evidence_scope": "minimum_wage_context_only",
                "requires_verified_recalculation_source": False,
                "automatic_revision_creation_allowed": False,
                "financial_execution_allowed": False}

    payroll_input = await db.scalar(
        select(PayrollInput).where(
            PayrollInput.company_id == company_id,
            PayrollInput.id == calculation.payroll_input_id,
        )
    )

    if payroll_input is None:
        raise PayrollCorrectionEvidenceError(
            "Payroll input not found"
        )

    slices = (
        await db.scalars(
            select(PayrollInputSalarySlice)
            .where(
                PayrollInputSalarySlice.company_id
                == company_id,
                PayrollInputSalarySlice.payroll_input_id
                == payroll_input.id,
            )
            .order_by(PayrollInputSalarySlice.id)
        )
    ).all()

    lines = (
        await db.scalars(
            select(PayrollCalculationLine)
            .where(
                PayrollCalculationLine.company_id
                == company_id,
                PayrollCalculationLine.payroll_calculation_id
                == calculation.id,
            )
            .order_by(
                PayrollCalculationLine.line_no,
                PayrollCalculationLine.id,
            )
        )
    ).all()

    if not slices:
        raise PayrollCorrectionEvidenceError(
            "Verified salary slices are required"
        )

    if not lines:
        raise PayrollCorrectionEvidenceError(
            "Original calculation lines are required"
        )

    if any(
        item.currency_code != calculation.currency_code
        for item in slices
    ):
        raise PayrollCorrectionEvidenceError(
            "Salary slice currency mismatch"
        )

    if any(
        item.currency_code != calculation.currency_code
        for item in lines
    ):
        raise PayrollCorrectionEvidenceError(
            "Calculation line currency mismatch"
        )

    line_total = sum(
        (item.amount for item in lines),
        Decimal("0.00"),
    )

    if line_total != calculation.gross_amount:
        raise PayrollCorrectionEvidenceError(
            "Calculation lines do not reconcile to gross"
        )

    snapshot = {
        "schema": "payroll-correction-source-v1",
        "payroll_input": _model_snapshot(payroll_input),
        "salary_slices": [
            _model_snapshot(item)
            for item in slices
        ],
        "calculation": _model_snapshot(calculation),
        "calculation_lines": [
            _model_snapshot(item)
            for item in lines
        ],
    }

    return {
        **preflight,
        "source_fingerprint_sha256": _fingerprint(snapshot),
        "source_evidence_schema": snapshot["schema"],
        "salary_slice_count": len(slices),
        "calculation_line_count": len(lines),
        "gross_amount": str(calculation.gross_amount),
        "source_evidence_complete": False,
        "requires_verified_recalculation_source": True,
        "automatic_revision_creation_allowed": False,
        "financial_execution_allowed": False,
    }
