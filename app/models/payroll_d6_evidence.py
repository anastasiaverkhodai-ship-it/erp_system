"""Append-only factual evidence; review does not approve statutory field mapping."""
from datetime import datetime
from sqlalchemy import Integer,String,DateTime,ForeignKeyConstraint,UniqueConstraint,CheckConstraint,func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped,mapped_column
from app.core.database import Base


class PayrollD6Evidence(Base):
    __tablename__='payroll_d6_evidence'
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    company_id:Mapped[int]=mapped_column(Integer,nullable=False)
    payroll_period_id:Mapped[int]=mapped_column(Integer,nullable=False)
    employment_contract_id:Mapped[int]=mapped_column(Integer,nullable=False)
    revision:Mapped[int]=mapped_column(Integer,nullable=False)
    supersedes_id:Mapped[int|None]=mapped_column(Integer)
    payload:Mapped[dict]=mapped_column(JSONB,nullable=False)
    request_key:Mapped[str]=mapped_column(String(120),nullable=False)
    fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
    created_by:Mapped[int]=mapped_column(Integer,nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
    __table_args__=(
        UniqueConstraint('company_id','id',name='uq_d6_evidence_company_id'),
        UniqueConstraint('company_id','request_key',name='uq_d6_evidence_request'),
        UniqueConstraint('company_id','payroll_period_id','employment_contract_id','revision',name='uq_d6_evidence_revision'),
        ForeignKeyConstraint(['company_id','payroll_period_id'],['payroll_periods.company_id','payroll_periods.id'],name='fk_d6_evidence_period',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','employment_contract_id'],['employment_contracts.company_id','employment_contracts.id'],name='fk_d6_evidence_contract',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','supersedes_id'],['payroll_d6_evidence.company_id','payroll_d6_evidence.id'],name='fk_d6_evidence_previous',ondelete='RESTRICT'),
        ForeignKeyConstraint(['created_by'],['users.id'],name='fk_d6_evidence_creator',ondelete='RESTRICT'),
        CheckConstraint('revision>0',name='ck_d6_evidence_revision'),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'",name='ck_d6_evidence_fingerprint'),
        CheckConstraint("jsonb_typeof(payload)='object'",name='ck_d6_evidence_payload'),
    )


class PayrollD6EvidenceReview(Base):
    __tablename__='payroll_d6_evidence_reviews'
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    company_id:Mapped[int]=mapped_column(Integer,nullable=False)
    evidence_id:Mapped[int]=mapped_column(Integer,nullable=False)
    decision:Mapped[str]=mapped_column(String(16),nullable=False)
    review_reference:Mapped[str]=mapped_column(String(500),nullable=False)
    reviewed_by:Mapped[int]=mapped_column(Integer,nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
    __table_args__=(
        UniqueConstraint('company_id','evidence_id',name='uq_d6_review_evidence'),
        ForeignKeyConstraint(['company_id','evidence_id'],['payroll_d6_evidence.company_id','payroll_d6_evidence.id'],name='fk_d6_review_evidence',ondelete='RESTRICT'),
        ForeignKeyConstraint(['reviewed_by'],['users.id'],name='fk_d6_review_reviewer',ondelete='RESTRICT'),
        CheckConstraint("decision IN ('approved','rejected')",name='ck_d6_review_decision'),
        CheckConstraint('length(trim(review_reference))>=10',name='ck_d6_review_reference'),
    )


# Keep metadata-created PostgreSQL schemas protected as well as migrated databases.
from sqlalchemy import DDL, event
from app.models.payroll_d6_evidence_guards import D6_GUARD_SQL
event.listen(PayrollD6EvidenceReview.__table__, "after_create", DDL(D6_GUARD_SQL).execute_if(dialect="postgresql"))
for _table in (PayrollD6Evidence.__table__, PayrollD6EvidenceReview.__table__):
    event.listen(PayrollD6EvidenceReview.__table__, "after_create", DDL(
        f"CREATE TRIGGER d6_evidence_write BEFORE INSERT OR UPDATE OR DELETE ON {_table.name} FOR EACH ROW EXECUTE FUNCTION erp_d6_evidence_guard()"
    ).execute_if(dialect="postgresql"))
    event.listen(PayrollD6EvidenceReview.__table__, "after_create", DDL(
        f"CREATE TRIGGER d6_evidence_truncate BEFORE TRUNCATE ON {_table.name} FOR EACH STATEMENT EXECUTE FUNCTION erp_d6_evidence_guard()"
    ).execute_if(dialect="postgresql"))
