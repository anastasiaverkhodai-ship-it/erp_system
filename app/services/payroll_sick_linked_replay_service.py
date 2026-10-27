"""Read-only payroll-linked sick correction replay.

This service computes a candidate revised payroll result.
It neither persists a revision nor changes journals, payments,
tax remittances or insurance funding.
"""
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollPeriod,
)
from app.models.leave_request import LeaveRequest
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation

from app.services.payroll_sick_payroll_replay import (
    replay_sick_payroll,
    SickPayrollReplayError,
)


@dataclass(frozen=True)
class LinkedSickPayrollReplay:
    company_id: int
    original_payroll_id: int
    original_sick_id: int
    replacement_sick_id: int
    original_gross: Decimal
    replacement_gross: Decimal
    gross_delta: Decimal
    lines: tuple
    assessment: dict


async def preview_linked_sick_payroll_replay(
    db,
    *,
    company_id: int,
    original_payroll_id: int,
    original_sick_id: int,
    replacement_sick_id: int,
):
    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == original_payroll_id,
        )
    )

    if calculation is None:
        raise SickPayrollReplayError(
            "Original payroll calculation not found"
        )

    original = await db.scalar(
        select(PayrollSickLeaveCalculation).where(
            PayrollSickLeaveCalculation.company_id == company_id,
            PayrollSickLeaveCalculation.id == original_sick_id,
        )
    )

    replacement = await db.scalar(
        select(PayrollSickLeaveCalculation).where(
            PayrollSickLeaveCalculation.company_id == company_id,
            PayrollSickLeaveCalculation.id == replacement_sick_id,
        )
    )

    if original is None or replacement is None:
        raise SickPayrollReplayError(
            "Sick source or replacement not found"
        )

    if (
        original.id == replacement.id
        or replacement.supersedes_id != original.id
        or replacement.revision != original.revision + 1
        or original.employment_contract_id
            != calculation.employment_contract_id
        or replacement.employment_contract_id
            != calculation.employment_contract_id
    ):
        raise SickPayrollReplayError(
            "Sick correction lineage or contract mismatch"
        )

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == calculation.payroll_period_id,
        )
    )

    leave = await db.scalar(
        select(LeaveRequest).where(
            LeaveRequest.company_id == company_id,
            LeaveRequest.id == original.leave_request_id,
        )
    )

    if period is None or leave is None:
        raise SickPayrollReplayError(
            "Payroll period or leave evidence not found"
        )

    lines = (
        await db.scalars(
            select(PayrollCalculationLine)
            .where(
                PayrollCalculationLine.company_id == company_id,
                PayrollCalculationLine.payroll_calculation_id
                    == original_payroll_id,
            )
            .order_by(
                PayrollCalculationLine.line_no,
                PayrollCalculationLine.id,
            )
        )
    ).all()

    matching = [
        line for line in lines
        if getattr(
            line, "source_sick_leave_calculation_id", None
        ) == original_sick_id
    ]

    if len(matching) != 1:
        raise SickPayrollReplayError(
            "Exactly one original sick-pay payroll line required"
        )

    gross, replayed_lines, assessment = replay_sick_payroll(
        calculation=calculation,
        lines=lines,
        original_sick=original,
        replacement_sick=replacement,
        leave=leave,
        period=period,
    )

    delta = gross - calculation.gross_amount

    if not delta.is_finite():
        raise SickPayrollReplayError(
            "Non-finite gross correction"
        )

    return LinkedSickPayrollReplay(
        company_id=company_id,
        original_payroll_id=original_payroll_id,
        original_sick_id=original_sick_id,
        replacement_sick_id=replacement_sick_id,
        original_gross=calculation.gross_amount,
        replacement_gross=gross,
        gross_delta=delta,
        lines=tuple(replayed_lines),
        assessment=assessment,
    )
