"""Attribute severance taxes from sealed calculated sources, never proposal figures."""
from decimal import Decimal as D, ROUND_HALF_UP
from sqlalchemy import select
from app.models.payroll_statutory import PayrollStatutoryResult as Result,PayrollStatutoryResultLine as Line,PayrollStatutoryLineSource as Source
from app.models.payroll import PayrollCalculationLine
from app.services.payroll_severance_net_tax_settlement import SeveranceFinancialSnapshot
from app.services.payroll_severance_trusted_classification import require_trusted_severance_snapshot


async def load_calculated_severance_financial(db, *, company_id, calculation_id, income_date, policies, historical=False):
    if historical:
        from app.models.payroll_severance_statutory_record import PayrollSeveranceStatutoryRecord
        from app.services.payroll_severance_snapshot_persistence_service import snapshot_from_record
        from app.services.payroll_severance_snapshot_integration_service import preview_severance_statutory_snapshot
        from app.services.payroll_severance_trusted_classification import TRUSTED_POLICIES
        record=await db.scalar(select(PayrollSeveranceStatutoryRecord).where(
            PayrollSeveranceStatutoryRecord.company_id==company_id,PayrollSeveranceStatutoryRecord.payroll_calculation_id==calculation_id))
        if record is None:raise ValueError('Historical severance classification required')
        trusted=snapshot_from_record(record)
        expected=await preview_severance_statutory_snapshot(db,company_id=company_id,
            payroll_calculation_id=calculation_id,income_date=income_date,policies=TRUSTED_POLICIES)
        if expected!=trusted:raise ValueError('Historical severance classification does not reconcile')
    else:
        trusted=await require_trusted_severance_snapshot(db,company_id=company_id,payroll_calculation_id=calculation_id)
    if trusted is None or trusted.income_date!=income_date:
        raise ValueError('Trusted severance classification and income date required')
    result=await db.scalar(select(Result).where(Result.company_id==company_id,Result.payroll_calculation_id==calculation_id))
    if result is None or result.currency_code!='UAH':raise ValueError('Calculated UAH statutory results required')
    lines=list((await db.scalars(select(Line).where(Line.company_id==company_id,Line.payroll_statutory_result_id==result.id))).all())
    from app.services.payroll_accounting_service import _component_amounts,_validate_source_reconciliation
    from app.models.payroll import PayrollCalculation
    calculation=await db.get(PayrollCalculation,calculation_id)
    components=_component_amounts(lines)
    _validate_source_reconciliation(calculation=calculation,statutory=result,components=components)
    severance_ids=set((await db.scalars(select(PayrollCalculationLine.id).where(
        PayrollCalculationLine.company_id==company_id,PayrollCalculationLine.payroll_calculation_id==calculation_id,
        PayrollCalculationLine.line_type=='severance'))).all())
    source_rows=(await db.scalars(select(Source).where(Source.company_id==company_id,
        Source.payroll_calculation_line_id.in_(severance_ids)))).all()
    amounts={component:D(0) for component in components}
    if source_rows:
        from app.models.payroll_statutory import PayrollDatedStatutorySeal
        seal=await db.scalar(select(PayrollDatedStatutorySeal).where(
            PayrollDatedStatutorySeal.company_id==company_id,PayrollDatedStatutorySeal.payroll_statutory_result_id==result.id))
        if seal is None:raise ValueError('Completed dated tax source seal required')
        linked={row.statutory_line_id for row in source_rows}
        for line in lines:
            if line.id not in linked:continue
            all_sources=(await db.scalars(select(Source).where(Source.company_id==company_id,
                Source.statutory_line_id==line.id))).all()
            if any(row.payroll_calculation_line_id not in severance_ids for row in all_sources):
                raise ValueError('Dated severance tax line must identify its separate income class')
            if line.component=='unified_social_contribution' and (line.amount or line.base_amount):
                raise ValueError('Severance must not contribute to the USC base')
            amounts[line.component]+=line.amount
        for component in amounts:
            linked_lines={line.id for line in lines if line.component==component}
            if sum((row.source_amount for row in source_rows if row.statutory_line_id in linked_lines),D(0))!=trusted.severance_gross:
                raise ValueError('Dated severance source coverage is incomplete')
    else:
        if len(lines)!=3 or len({line.component for line in lines})!=3:
            raise ValueError('Multiple tax lines require sealed dated income attribution')
        for line in lines:
            if line.component=='unified_social_contribution':continue
            expected=D('.18') if line.component=='personal_income_tax' else D('.05')
            if line.rate!=expected or line.base_amount<trusted.severance_gross:
                raise ValueError('Severance requires ordinary other-income tax treatment')
            ordinary_tax=((line.base_amount-trusted.severance_gross)*line.rate).quantize(D('.01'),rounding=ROUND_HALF_UP)
            amounts[line.component]=line.amount-ordinary_tax
    pit=amounts['personal_income_tax'];levy=amounts['military_levy']
    snapshot=SeveranceFinancialSnapshot(gross=trusted.severance_gross,personal_income_tax=pit,military_levy=levy,
        employer_contribution=amounts['unified_social_contribution'],net=trusted.severance_gross-pit-levy)
    snapshot.validate()
    if snapshot.net>result.net_amount:raise ValueError('Severance net exceeds calculated payroll net')
    return snapshot
