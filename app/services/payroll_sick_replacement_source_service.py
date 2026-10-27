"""Materialize a rederived sick source inside the correction transaction.

Internal building block: the caller must atomically complete payroll, tax and
settlement integration before committing. This is deliberately not an API.
"""
from sqlalchemy import select
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.services.payroll_sick_history_service import calculate_sick_from_history
from app.services.payroll_sick_leave_service import PayrollSickLeaveConflictError
from app.services.payroll_mutation_guard import serialized_payroll_mutation


@serialized_payroll_mutation(PayrollSickLeaveConflictError)
async def materialize_sick_replacement(db, *, company_id, original_calculation_id,
                                      insurance_evidence_id, calculated_by, excluded_source_ids=()):
    original = await db.scalar(select(PayrollSickLeaveCalculation).where(
        PayrollSickLeaveCalculation.company_id == company_id,
        PayrollSickLeaveCalculation.id == original_calculation_id,
    ).with_for_update())
    if original is None:
        raise PayrollSickLeaveConflictError('Original sick calculation not found')
    row, sources = await calculate_sick_from_history(
        db, company_id=company_id, leave_request_id=original.leave_request_id,
        insurance_evidence_id=insurance_evidence_id, calculated_by=calculated_by,
        _preview_replacement_of=original.id, _correction_excluded_ids=excluded_source_ids,
    )
    row.revision = original.revision + 1
    row.supersedes_id = original.id
    async with db.begin_nested():
        db.add(row)
        await db.flush()
        for source in sources:
            source.sick_leave_calculation_id = row.id
        db.add_all(sources)
        await db.flush()
    return row
