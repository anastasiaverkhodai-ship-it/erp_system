"""Reviewed special-service facts. No approval of statutory codes or XML fields."""
from sqlalchemy import select, func, or_, text
from app.models.company import Company
from app.models.user import User
from app.models.payroll import PayrollPeriod
from app.models.employment_contract import EmploymentContract
from app.models.payroll_d6_evidence import PayrollD6Evidence as Evidence, PayrollD6EvidenceReview as Review
from app.schemas.payroll_d6_evidence import D6EvidenceCreate, D6EvidenceReviewCreate
from app.services.payroll_regulatory_d5_d6_workflow import digest
from app.services.payroll_deduction_rule_approval_write_service import _require_permission


class D6MigrationRequired(ValueError):
    pass


async def schema_available(db):
    return bool(await db.scalar(text("SELECT to_regclass('payroll_d6_evidence') IS NOT NULL AND to_regclass('payroll_d6_evidence_reviews') IS NOT NULL")))


async def require_schema(db):
    if not await schema_available(db):
        raise D6MigrationRequired("D6 evidence migration p13d6ev261104 is required; no data was changed")


async def authorize(db, company_id, actor_id):
    await _require_permission(db=db, company_id=company_id, actor_id=actor_id, permission="employees.manage")
    if not await db.scalar(select(User.id).where(User.id==actor_id, User.is_active.is_(True))):
        raise ValueError("Active actor required")


async def lock_company(db, company_id):
    if not await db.scalar(select(Company.id).where(Company.id==company_id).with_for_update()):
        raise ValueError("Company not found")


async def current(db, company_id, period_id, contract_id):
    return await db.scalar(select(Evidence).join(Review, (Review.company_id==Evidence.company_id)&(Review.evidence_id==Evidence.id)).where(
        Evidence.company_id==company_id, Evidence.payroll_period_id==period_id,
        Evidence.employment_contract_id==contract_id, Review.decision=="approved"
    ).order_by(Evidence.revision.desc()).limit(1))


async def validate_context(db, company_id, data):
    period=await db.scalar(select(PayrollPeriod).where(PayrollPeriod.company_id==company_id,PayrollPeriod.id==data.payroll_period_id))
    contract=await db.scalar(select(EmploymentContract).where(EmploymentContract.company_id==company_id,EmploymentContract.id==data.employment_contract_id))
    if (not period or not contract or contract.status=="cancelled" or contract.start_date>period.end_date
            or (contract.end_date and contract.end_date<period.start_date)):
        raise ValueError("Company-scoped employment overlapping the period required")
    for interval in data.intervals:
        if (interval.start_date<max(period.start_date,contract.start_date) or interval.end_date>period.end_date
                or (contract.end_date and interval.end_date>contract.end_date)):
            raise ValueError("Service interval outside payroll period or employment")
    return period, contract


async def create_evidence(db, *, company_id, actor_id, data: D6EvidenceCreate):
    await authorize(db, company_id, actor_id)
    await require_schema(db)
    await lock_company(db, company_id)
    payload=data.model_dump(mode="json", exclude={"request_key"})
    fingerprint=digest(payload)
    previous_request=await db.scalar(select(Evidence).where(Evidence.company_id==company_id, Evidence.request_key==data.request_key))
    if previous_request:
        if previous_request.fingerprint!=fingerprint or previous_request.created_by!=actor_id:
            raise ValueError("Request key already belongs to different evidence")
        return previous_request
    period, contract = await validate_context(db, company_id, data)
    approved=await current(db,company_id,period.id,contract.id)
    if data.supersedes_id!=(approved.id if approved else None):
        raise ValueError("Correction must supersede current approved evidence")
    revision=1+(await db.scalar(select(func.max(Evidence.revision)).where(Evidence.company_id==company_id,
        Evidence.payroll_period_id==period.id,Evidence.employment_contract_id==contract.id)) or 0)
    record=Evidence(company_id=company_id,payroll_period_id=period.id,employment_contract_id=contract.id,
        revision=revision,supersedes_id=data.supersedes_id,payload=payload,request_key=data.request_key,
        fingerprint=fingerprint,created_by=actor_id)
    db.add(record)
    await db.flush()
    return record


async def review_evidence(db, *, company_id, actor_id, evidence_id, data: D6EvidenceReviewCreate):
    await authorize(db,company_id,actor_id)
    await require_schema(db)
    await lock_company(db,company_id)
    evidence=await db.scalar(select(Evidence).where(Evidence.company_id==company_id,Evidence.id==evidence_id))
    if not evidence: raise ValueError("Company-scoped evidence not found")
    if evidence.created_by==actor_id: raise ValueError("Independent reviewer required")
    decision="approved" if data.approved else "rejected"
    existing=await db.scalar(select(Review).where(Review.company_id==company_id,Review.evidence_id==evidence.id))
    if existing:
        if (existing.reviewed_by,existing.decision,existing.review_reference)!=(actor_id,decision,data.review_reference):
            raise ValueError("Evidence already reviewed; create a correction")
        return existing
    if digest(evidence.payload)!=evidence.fingerprint: raise ValueError("Evidence fingerprint mismatch")
    evidence_data = D6EvidenceCreate.model_validate({**evidence.payload,"request_key":evidence.request_key})
    if (evidence_data.payroll_period_id, evidence_data.employment_contract_id, evidence_data.supersedes_id) != (evidence.payroll_period_id, evidence.employment_contract_id, evidence.supersedes_id):
        raise ValueError("Evidence identity mismatch")
    if data.approved:
        await validate_context(db, company_id, evidence_data)
    approved=await current(db,company_id,evidence.payroll_period_id,evidence.employment_contract_id)
    if data.approved and evidence.supersedes_id!=(approved.id if approved else None):
        raise ValueError("Stale correction: approved evidence changed")
    review=Review(company_id=company_id,evidence_id=evidence.id,decision=decision,
        review_reference=data.review_reference,reviewed_by=actor_id)
    db.add(review)
    await db.flush()
    return review


async def period_evidence(db, *, company_id, payroll_period_id):
    period=await db.scalar(select(PayrollPeriod).where(PayrollPeriod.company_id==company_id,PayrollPeriod.id==payroll_period_id))
    if not period: raise ValueError("Company-scoped payroll period not found")
    contracts=list(await db.scalars(select(EmploymentContract.id).where(EmploymentContract.company_id==company_id,
        EmploymentContract.status!="cancelled",EmploymentContract.start_date<=period.end_date,
        or_(EmploymentContract.end_date.is_(None),EmploymentContract.end_date>=period.start_date)).order_by(EmploymentContract.id)))
    if not await schema_available(db):
        return {"company_id":company_id,"payroll_period_id":period.id,"records":[],
            "missing_contract_ids":contracts,"coverage":"MIGRATION_REQUIRED",
            "applicability":"NOT_DETERMINED","normative_mapping":"NOT_APPROVED",
            "official_statutory_generation":"BLOCKED"}
    records=[]
    missing=[]
    for contract_id in contracts:
        item=await current(db,company_id,period.id,contract_id)
        if not item:
            missing.append(contract_id)
            continue
        if digest(item.payload)!=item.fingerprint: raise ValueError("Evidence fingerprint mismatch")
        review=await db.scalar(select(Review).where(Review.company_id==company_id,Review.evidence_id==item.id))
        records.append({"evidence_id":item.id,"employment_contract_id":contract_id,"revision":item.revision,
            "source_sha256":item.fingerprint,"payload":item.payload,"review_id":review.id,
            "reviewed_by":review.reviewed_by,"review_reference":review.review_reference})
    applies=any(x["payload"]["applies"] for x in records)
    return {"company_id":company_id,"payroll_period_id":period.id,"records":records,
        "missing_contract_ids":missing,"coverage":"INCOMPLETE" if missing or not contracts else "COMPLETE",
        "applicability":"APPLICABLE" if applies else "NOT_DETERMINED" if missing or not contracts else "NOT_APPLICABLE",
        "normative_mapping":"NOT_APPROVED","official_statutory_generation":"BLOCKED"}


async def get_evidence(db, *, company_id, evidence_id):
    await require_schema(db)
    item=await db.scalar(select(Evidence).where(Evidence.company_id==company_id,Evidence.id==evidence_id))
    if not item: raise ValueError("Company-scoped evidence not found")
    if digest(item.payload)!=item.fingerprint: raise ValueError("Evidence fingerprint mismatch")
    review=await db.scalar(select(Review).where(Review.company_id==company_id,Review.evidence_id==item.id))
    return {"id":item.id,"company_id":item.company_id,"revision":item.revision,
        "supersedes_id":item.supersedes_id,"payload":item.payload,"fingerprint":item.fingerprint,
        "created_by":item.created_by,"created_at":item.created_at,
        "review":None if review is None else {"id":review.id,"decision":review.decision,
            "reviewed_by":review.reviewed_by,"review_reference":review.review_reference,"created_at":review.created_at},
        "official_statutory_generation":"BLOCKED"}
