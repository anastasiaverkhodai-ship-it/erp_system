"""Financial reconciliation of independently verified payroll snapshots.

Internal calculation only. No posting, payout, authorization or commit.
The caller must obtain both snapshots from trusted server calculations.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.services.payroll_sick_correction_plan import (
    PayrollFinancialSnapshot,
    plan_sick_correction,
    SickCorrectionPlanError,
)


class SickFinancialReplayError(ValueError):
    pass


@dataclass(frozen=True)
class SickFinancialReplayResult:
    original_gross: Decimal
    replacement_gross: Decimal
    gross_delta: Decimal
    net_delta: Decimal
    employee_due: Decimal
    employee_overpayment: Decimal
    insurer_funding_due: Decimal
    insurer_funding_excess: Decimal
    journal_lines: tuple


def reconcile_sick_payroll_replay(
    *,
    replay,
    original_financial: PayrollFinancialSnapshot,
    replacement_financial: PayrollFinancialSnapshot,
    employee_settled: Decimal,
    insurer_received: Decimal,
    employee_returned: Decimal = Decimal("0.00"),
    insurer_returned: Decimal = Decimal("0.00"),
):
    if not isinstance(
        original_financial, PayrollFinancialSnapshot
    ) or not isinstance(
        replacement_financial, PayrollFinancialSnapshot
    ):
        raise SickFinancialReplayError(
            "Verified financial snapshots required"
        )

    try:
        original_financial.validate()
        replacement_financial.validate()
    except SickCorrectionPlanError as exc:
        raise SickFinancialReplayError(str(exc)) from exc

    if (
        replay.original_gross != original_financial.gross
        or replay.replacement_gross != replacement_financial.gross
    ):
        raise SickFinancialReplayError(
            "Payroll replay and financial snapshots do not reconcile"
        )

    if (
        replay.gross_delta
        != replacement_financial.gross
        - original_financial.gross
    ):
        raise SickFinancialReplayError(
            "Gross correction delta mismatch"
        )

    try:
        plan = plan_sick_correction(
            original=original_financial,
            replacement=replacement_financial,
            employee_settled=employee_settled,
            insurer_received=insurer_received,
            employee_returned=employee_returned,
            insurer_returned=insurer_returned,
        )
        journal_lines = plan.journal_sides()
    except SickCorrectionPlanError as exc:
        raise SickFinancialReplayError(str(exc)) from exc

    debit = sum(
        (line[1] for line in journal_lines),
        Decimal("0.00"),
    )
    credit = sum(
        (line[2] for line in journal_lines),
        Decimal("0.00"),
    )

    if debit != credit:
        raise SickFinancialReplayError(
            "Unbalanced financial correction"
        )

    return SickFinancialReplayResult(
        original_gross=original_financial.gross,
        replacement_gross=replacement_financial.gross,
        gross_delta=replay.gross_delta,
        net_delta=plan.net_delta,
        employee_due=plan.employee_due,
        employee_overpayment=plan.employee_overpayment,
        insurer_funding_due=plan.insurer_funding_due,
        insurer_funding_excess=plan.insurer_funding_excess,
        journal_lines=journal_lines,
    )
