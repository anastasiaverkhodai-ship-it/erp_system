from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.permissions import require_company_permission
from app.models.user import User
from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication, PayrollSickPayrollRevision
from app.models.company import Company
from app.schemas.payroll_sick_correction_application import SickCorrectionApply, SickCorrectionReturnCreate
from app.services.payroll_sick_correction_application_service import apply_sick_correction
from app.services.payroll_sick_return_service import register_sick_return
from app.services.payroll_sick_settlement_service import sick_settlement_summary
from app.services.payroll_sick_leave_service import PayrollSickLeaveError
from app.services.payroll_insurance_evidence_service import PayrollInsuranceEvidenceError
from app.services.payroll_statutory_service import PayrollStatutoryError
from app.services.payroll_accounting_service import PayrollAccountingError
from app.services.accounting_posting import AccountingPostingError

router = APIRouter(prefix='/companies/{company_id}/payroll-sick-corrections', tags=['Payroll sick corrections'])
ERRORS = (ValueError, PayrollSickLeaveError, PayrollInsuranceEvidenceError,
          PayrollStatutoryError, PayrollAccountingError, AccountingPostingError)


async def application_response(db, application, *, as_of=None):
    as_of = as_of or application.correction_date
    if as_of < application.correction_date:
        raise HTTPException(status_code=422, detail="Settlement date precedes this correction")
    await db.scalar(select(Company.id).where(Company.id==application.company_id).with_for_update(read=True))
    ids = set(application.payroll_calculation_ids)
    balances = []
    for identifier in sorted(ids):
        current_id = identifier
        seen = set()
        while True:
            if current_id in seen:
                raise ValueError('Cyclic sick correction lineage')
            seen.add(current_id)
            successor = await db.scalar(select(PayrollSickPayrollRevision.replacement_calculation_id)
                .join(PayrollSickCorrectionApplication,
                    (PayrollSickCorrectionApplication.company_id==PayrollSickPayrollRevision.company_id) &
                    (PayrollSickCorrectionApplication.id==PayrollSickPayrollRevision.application_id))
                .where(PayrollSickPayrollRevision.company_id==application.company_id,
                    PayrollSickPayrollRevision.original_calculation_id==current_id,
                    PayrollSickCorrectionApplication.correction_date<=as_of))
            if successor is None:
                break
            current_id = successor
        balances.append(await sick_settlement_summary(db, company_id=application.company_id,
            calculation_id=current_id, as_of=as_of))
    return dict(id=application.id, company_id=application.company_id, status='applied',
        original_sick_id=application.original_sick_id, replacement_sick_id=application.replacement_sick_id,
        correction_date=application.correction_date, reason=application.reason,
        payroll_calculation_ids=sorted(ids), source_replacements=application.source_replacements, settlements=balances)


@router.post('/apply', status_code=201)
async def apply(company_id:int, data:SickCorrectionApply, db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage')),
    accountant:User=Depends(require_company_permission('journal_entries.create'))):
    try:
        row = await apply_sick_correction(db, company_id=company_id, actor_id=actor.id, **data.model_dump())
        result = await application_response(db,row)
        await db.commit()
        return result
    except ERRORS as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise


@router.post('/returns', status_code=201)
async def register_return(company_id:int, data:SickCorrectionReturnCreate, db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.manage')),
    accountant:User=Depends(require_company_permission('journal_entries.create'))):
    try:
        row = await register_sick_return(db, company_id=company_id, actor_id=actor.id, **data.model_dump())
        result = dict(id=row.id, payroll_calculation_id=row.payroll_calculation_id,
            journal_entry_id=row.journal_entry_id, kind=row.kind, amount=row.amount)
        await db.commit()
        return result
    except ERRORS as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise


@router.get('/{application_id}')
async def read_application(company_id:int, application_id:int, as_of:date | None=None, db:AsyncSession=Depends(get_db),
    actor:User=Depends(require_company_permission('employees.read'))):
    row = await db.scalar(select(PayrollSickCorrectionApplication).where(
        PayrollSickCorrectionApplication.company_id==company_id, PayrollSickCorrectionApplication.id==application_id))
    if row is None:
        raise HTTPException(status_code=404, detail='Sick correction application not found')
    return await application_response(db,row, as_of=as_of or date.today())
