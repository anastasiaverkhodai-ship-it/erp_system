"""Persist a frozen payroll revision for an already applied sick correction.

No tax settlement, journal posting, payout or insurance transfer.
Caller retains transaction ownership and must authorize the actor.
"""
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
)
from app.models.payroll_sick_correction_application import (
    PayrollSickCorrectionApplication,
)
from app.services.payroll_sick_linked_replay_service import (
    preview_linked_sick_payroll_replay,
)
from app.services.payroll_correction_service import (
    create_payroll_correction_link,
)
from app.services.payroll_correction_source_evidence_service import (
    _canonical_value,
)
from app.services.payroll_sick_payroll_replay import (
    SickPayrollReplayError,
)
from app.services.payroll_correction_basis import (
    SEAL,
    verify_basis,
)
from app.services.payroll_correction_source_evidence_service import (
    _fingerprint,
)
from copy import deepcopy

from app.services.payroll_mutation_guard import (
    serialized_payroll_mutation,
)



def freeze_revision_assessment(assessment):
    """Seal the complete replayed payroll basis before persistence.

    This does not approve any financial settlement.
    """
    if not isinstance(assessment, dict):
        raise SickPayrollReplayError(
            "Payroll revision assessment must be a dictionary"
        )

    frozen = deepcopy(assessment)
    basis = frozen.get("correction_basis")

    if not isinstance(basis, dict):
        raise SickPayrollReplayError(
            "Missing revision correction basis"
        )

    payload = basis.get("payload")

    if not isinstance(payload, dict):
        raise SickPayrollReplayError(
            "Missing frozen correction payload"
        )

    digest = basis.get("sha256")

    if (
        not isinstance(digest, str)
        or digest != _fingerprint(payload)
    ):
        raise SickPayrollReplayError(
            "Revision basis fingerprint mismatch"
        )

    if basis.get("seal") not in (None, SEAL):
        raise SickPayrollReplayError(
            "Unsupported revision basis seal"
        )

    if "parent_source_fingerprint" not in basis:
        raise SickPayrollReplayError(
            "Original source fingerprint is missing"
        )

    basis["seal"] = SEAL
    return _canonical_value(frozen)


LINE_FIELDS = frozenset(
    col.name
    for col in PayrollCalculationLine.__table__.columns
) - frozenset({
    "id",
    "company_id",
    "payroll_calculation_id",
    "created_at",
})


def validate_revision_lines(lines, expected_gross, currency):
    """Require a complete, well-formed frozen line set."""
    if not lines:
        raise SickPayrollReplayError(
            "Payroll revision cannot have empty lines"
        )

    prepared = []
    numbers = set()
    gross = Decimal("0.00")
    sick_count = 0

    for source in lines:
        if not isinstance(source, dict):
            raise SickPayrollReplayError(
                "Replayed payroll lines must be dictionaries"
            )

        unknown = set(source) - LINE_FIELDS
        if unknown:
            raise SickPayrollReplayError(
                "Unsupported replayed payroll line fields: "
                + ", ".join(sorted(unknown))
            )

        data = {
            key: value
            for key, value in source.items()
            if key in LINE_FIELDS
        }

        number = data.get("line_no")
        if type(number) is not int or number <= 0:
            raise SickPayrollReplayError(
                "Invalid payroll line number"
            )

        if number in numbers:
            raise SickPayrollReplayError(
                "Duplicate payroll line number"
            )
        numbers.add(number)

        if data.get("currency_code") != currency:
            raise SickPayrollReplayError(
                "Payroll line currency mismatch"
            )

        amount = data.get("amount")
        if not isinstance(amount, Decimal):
            raise SickPayrollReplayError(
                "Replayed amount must be Decimal"
            )
        if not amount.is_finite():
            raise SickPayrollReplayError(
                "Non-finite payroll amount"
            )

        if data.get("line_type") == "sick_pay":
            sick_count += 1
            if data.get(
                "source_sick_leave_calculation_id"
            ) is None:
                raise SickPayrollReplayError(
                    "Sick-pay line has no sick source"
                )

        gross += amount
        prepared.append(data)

    if sick_count < 1:
        raise SickPayrollReplayError(
            "Payroll revision is missing sick-pay lines"
        )

    if numbers != set(range(1, len(lines) + 1)):
        raise SickPayrollReplayError(
            "Payroll line sequence is not contiguous"
        )

    if gross != expected_gross:
        raise SickPayrollReplayError(
            "Replayed payroll lines do not reconcile to gross"
        )

    return tuple(prepared)


@serialized_payroll_mutation(SickPayrollReplayError)
async def persist_sick_payroll_revision(
    db,
    *,
    company_id: int,
    application_id: int,
    original_payroll_id: int,
    created_by: int,
):
    """Write revised calculation, lines and correction link atomically.

    This is NOT financial settlement approval or tax recalculation.
    """

    if (
        type(created_by) is not int
        or created_by <= 0
    ):
        raise SickPayrollReplayError(
            "Valid authenticated actor required"
        )

    application = await db.scalar(
        select(PayrollSickCorrectionApplication)
        .where(
            PayrollSickCorrectionApplication.company_id
                == company_id,
            PayrollSickCorrectionApplication.id
                == application_id,
        )
        .with_for_update()
    )

    if application is None:
        raise SickPayrollReplayError(
            "Sick correction application not found"
        )

    original = await db.scalar(
        select(PayrollCalculation)
        .where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == original_payroll_id,
        )
        .with_for_update()
    )

    if original is None:
        raise SickPayrollReplayError(
            "Original payroll calculation not found"
        )

    # A previously saved next revision is not silently reused.
    next_revision = await db.scalar(
        select(PayrollCalculation.id)
        .where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.payroll_period_id
                == original.payroll_period_id,
            PayrollCalculation.employment_contract_id
                == original.employment_contract_id,
            PayrollCalculation.revision
                == original.revision + 1,
        )
        .limit(1)
    )

    if next_revision is not None:
        raise SickPayrollReplayError(
            "Next payroll revision already exists"
        )

    replay = await preview_linked_sick_payroll_replay(
        db,
        company_id=company_id,
        original_payroll_id=original.id,
        original_sick_id=application.original_sick_id,
        replacement_sick_id=application.replacement_sick_id,
    )

    lines = validate_revision_lines(
        replay.lines,
        replay.replacement_gross,
        original.currency_code,
    )

    if (
        replay.original_gross != original.gross_amount
        or replay.original_payroll_id != original.id
    ):
        raise SickPayrollReplayError(
            "Frozen original payroll mismatch"
        )

    # Do not allow source-changing revisions to rewrite
    # an already posted or disbursed calculation.
    from app.services.payroll_sick_correction_financial_guard import (
        require_sick_replacement_financially_clear,
    )
    await require_sick_replacement_financially_clear(
        db,
        company_id=company_id,
        original_calculation_id=application.original_sick_id,
    )

    async with db.begin_nested():
        replacement = PayrollCalculation(
            company_id=company_id,
            payroll_period_id=original.payroll_period_id,
            payroll_input_id=original.payroll_input_id,
            employment_contract_id=original.employment_contract_id,
            revision=original.revision + 1,
            status=original.status,
            currency_code=original.currency_code,
            gross_amount=replay.replacement_gross,
            minimum_wage_assessment=freeze_revision_assessment(
                replay.assessment
            ),
            calculated_by=created_by,
        )

        db.add(replacement)
        await db.flush()

        for data in lines:
            db.add(
                PayrollCalculationLine(
                    company_id=company_id,
                    payroll_calculation_id=replacement.id,
                    **data,
                )
            )

        await db.flush()

        persisted_lines = (
            await db.scalars(
                select(PayrollCalculationLine)
                .where(
                    PayrollCalculationLine.company_id == company_id,
                    PayrollCalculationLine.payroll_calculation_id
                        == replacement.id,
                )
                .order_by(
                    PayrollCalculationLine.line_no,
                    PayrollCalculationLine.id,
                )
            )
        ).all()

        try:
            verify_basis(replacement, persisted_lines)
        except Exception as exc:
            raise SickPayrollReplayError(
                "Persisted revised payroll does not reconcile "
                "with its frozen creation-time basis"
            ) from exc

        # Existing service validates current-version lineage,
        # company, input, period, journals and payouts.
        correction = await create_payroll_correction_link(
            db,
            company_id=company_id,
            original_payroll_calculation_id=original.id,
            replacement_payroll_calculation_id=replacement.id,
            request_key=application.request_key,
            reason=application.reason,
            created_by=created_by,
        )

        await db.flush()

    return replacement, correction
