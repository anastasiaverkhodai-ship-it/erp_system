"""Select the latest sick-pay source without altering historical references."""
from sqlalchemy import select
from sqlalchemy.orm import aliased
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation


def current_sick_calculation_filter(calculation=PayrollSickLeaveCalculation):
    successor = aliased(PayrollSickLeaveCalculation)
    return ~select(successor.id).where(
        successor.company_id == calculation.company_id,
        successor.supersedes_id == calculation.id,
    ).correlate(calculation).exists()
