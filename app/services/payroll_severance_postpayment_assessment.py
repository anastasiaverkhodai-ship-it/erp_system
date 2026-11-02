"""Read-only severance post-payment financial assessment.

Uses persisted severance revisions, real payroll calculation lines,
and posted disbursement journals. Does not authorize payment,
tax correction, refund, statutory export or journal posting.
"""

from dataclasses import dataclass
from decimal import Decimal
from sqlalchemy import select, and_

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
)
from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.journal_entry import JournalEntry
from app.services.payroll_termination_severance_lines import (
    require_revision_chain,
)


class SeveranceSettlementAssessmentError(ValueError):
    pass


@dataclass(frozen=True)
class SeveranceSettlementAssessment:
    company_id: int
    payroll_input_id: int
    latest_source_id: int
    last_calculated_source_id: int | None
    original_gross: Decimal
    latest_gross: Decimal
    gross_difference: Decimal
    posted_disbursement_count: int
    posted_disbursement_total: Decimal
    classification: str
    review_required: bool
    payment_authorized: bool = False
    tax_correction_authorized: bool = False
    journal_posting_authorized: bool = False


def money(value):
    if isinstance(value, bool):
        raise SeveranceSettlementAssessmentError(
            "Boolean is not money"
        )
    try:
        amount = Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise SeveranceSettlementAssessmentError(
            "Invalid financial amount"
        ) from exc

    if (
        not amount.is_finite()
        or amount != amount.quantize(Decimal("0.01"))
    ):
        raise SeveranceSettlementAssessmentError(
            "Financial amount requires finite two-decimal money"
        )
    return amount


def classify_difference(*, latest, calculated, posted_count):
    latest = money(latest)
    calculated = money(calculated)

    if latest < 0 or calculated < 0:
        raise SeveranceSettlementAssessmentError(
            "Negative gross source is invalid"
        )

    if type(posted_count) is not int or posted_count < 0:
        raise SeveranceSettlementAssessmentError(
            "Invalid posted payment count"
        )

    delta = latest - calculated

    if delta > 0:
        kind = (
            "POSTPAYMENT_ADDITIONAL_REVIEW"
            if posted_count
            else "ADDITIONAL_ACCRUAL_REVIEW"
        )
    elif delta < 0:
        kind = (
            "POSTPAYMENT_OVERPAYMENT_REVIEW"
            if posted_count
            else "REDUCED_ACCRUAL_REVIEW"
        )
    elif posted_count:
        kind = "POSTED_AMOUNT_UNCHANGED"
    else:
        kind = "NO_POSTED_PAYMENT"

    return delta, kind


async def assess_severance_postpayment(
    db,
    *,
    company_id: int,
    payroll_input_id: int,
):
    """Compare real source versions and posted settlement evidence.

    No mutations or flushes. An assessment is not a correction
    instruction and cannot be used to derive net payment amounts.
    """

    if (
        type(company_id) is not int
        or company_id <= 0
        or type(payroll_input_id) is not int
        or payroll_input_id <= 0
    ):
        raise SeveranceSettlementAssessmentError(
            "Valid company and payroll input required"
        )

    sources = list((
        await db.scalars(
            select(PayrollTerminationSeverance)
            .where(
                PayrollTerminationSeverance.company_id == company_id,
                PayrollTerminationSeverance.payroll_input_id
                == payroll_input_id,
            )
            .order_by(
                PayrollTerminationSeverance.revision,
                PayrollTerminationSeverance.id,
            )
        )
    ).all())

    if not sources:
        raise SeveranceSettlementAssessmentError(
            "No verified severance source"
        )

    require_revision_chain(sources)

    latest = sources[-1]
    source_by_id = {source.id: source for source in sources}

    if len(source_by_id) != len(sources):
        raise SeveranceSettlementAssessmentError(
            "Duplicate severance source IDs"
        )

    calculated_rows = list((
        await db.execute(
            select(
                PayrollCalculation.id,
                PayrollCalculation.revision,
                PayrollCalculationLine.source_termination_severance_id,
            )
            .join(
                PayrollCalculationLine,
                and_(
                    PayrollCalculationLine.company_id
                    == PayrollCalculation.company_id,
                    PayrollCalculationLine.payroll_calculation_id
                    == PayrollCalculation.id,
                ),
            )
            .where(
                PayrollCalculation.company_id == company_id,
                PayrollCalculation.payroll_input_id
                == payroll_input_id,
                PayrollCalculationLine.line_type == "severance",
            )
            .order_by(
                PayrollCalculation.revision,
                PayrollCalculation.id,
            )
        )
    ).all())

    if len({
        row[0] for row in calculated_rows
    }) != len(calculated_rows):
        raise SeveranceSettlementAssessmentError(
            "Multiple severance lines in a payroll revision"
        )

    for _, _, source_id in calculated_rows:
        if source_id not in source_by_id:
            raise SeveranceSettlementAssessmentError(
                "Payroll severance source is outside verified chain"
            )

    calculated_source = (
        source_by_id[calculated_rows[-1][2]]
        if calculated_rows else None
    )

    calculation_ids = list((
        await db.scalars(
            select(PayrollCalculation.id).where(
                PayrollCalculation.company_id == company_id,
                PayrollCalculation.payroll_input_id == payroll_input_id,
            )
        )
    ).all())

    posted_count = 0
    posted_amount = Decimal("0.00")

    if calculation_ids:
        payouts = list((
            await db.execute(
                select(
                    PayrollDisbursement.id,
                    PayrollDisbursement.amount,
                    PayrollDisbursement.withheld_amount,
                    JournalEntry.id,
                    JournalEntry.status,
                )
                .outerjoin(
                    JournalEntry,
                    and_(
                        JournalEntry.company_id
                        == PayrollDisbursement.company_id,
                        JournalEntry.payroll_disbursement_id
                        == PayrollDisbursement.id,
                        JournalEntry.reversal_of_id.is_(None),
                    ),
                )
                .where(
                    PayrollDisbursement.company_id == company_id,
                    PayrollDisbursement.payroll_calculation_id.in_(
                        calculation_ids
                    ),
                )
            )
        ).all())

        observed = set()

        for payment_id, amount, withheld, journal_id, status in payouts:
            if payment_id in observed:
                raise SeveranceSettlementAssessmentError(
                    "Duplicate journal association for payroll payout"
                )

            observed.add(payment_id)

            if journal_id is None:
                raise SeveranceSettlementAssessmentError(
                    "Payroll payout without journal needs review"
                )

            state = getattr(status, "value", status)

            if state == "posted":
                posted_count += 1
                posted_amount += money(amount) + money(withheld or 0)
            elif state != "reversed":
                raise SeveranceSettlementAssessmentError(
                    "Non-final payroll payment journal needs review"
                )

    original_gross = money(
        calculated_source.gross_amount
        if calculated_source is not None
        else Decimal("0.00")
    )
    latest_gross = money(latest.gross_amount)

    difference, classification = classify_difference(
        latest=latest_gross,
        calculated=original_gross,
        posted_count=posted_count,
    )

    return SeveranceSettlementAssessment(
        company_id=company_id,
        payroll_input_id=payroll_input_id,
        latest_source_id=latest.id,
        last_calculated_source_id=(
            calculated_source.id
            if calculated_source is not None
            else None
        ),
        original_gross=original_gross,
        latest_gross=latest_gross,
        gross_difference=difference,
        posted_disbursement_count=posted_count,
        posted_disbursement_total=posted_amount,
        classification=classification,
        review_required=(
            difference != 0 or posted_count > 0
        ),
    )
