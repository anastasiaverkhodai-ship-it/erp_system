"""Immutable reviewed D6 factual evidence, without normative mapping approval."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision="p13d6ev261104"
down_revision="p13exec261103"
branch_labels=None
depends_on=None


def upgrade():
    op.create_table("payroll_d6_evidence",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("company_id",sa.Integer(),nullable=False),
        sa.Column("payroll_period_id",sa.Integer(),nullable=False),
        sa.Column("employment_contract_id",sa.Integer(),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False),
        sa.Column("supersedes_id",sa.Integer()),
        sa.Column("payload",postgresql.JSONB(),nullable=False),
        sa.Column("request_key",sa.String(120),nullable=False),
        sa.Column("fingerprint",sa.String(64),nullable=False),
        sa.Column("created_by",sa.Integer(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint("company_id","id",name="uq_d6_evidence_company_id"),
        sa.UniqueConstraint("company_id","request_key",name="uq_d6_evidence_request"),
        sa.UniqueConstraint("company_id","payroll_period_id","employment_contract_id","revision",name="uq_d6_evidence_revision"),
        sa.ForeignKeyConstraint(["company_id","payroll_period_id"],["payroll_periods.company_id","payroll_periods.id"],name="fk_d6_evidence_period",ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["company_id","employment_contract_id"],["employment_contracts.company_id","employment_contracts.id"],name="fk_d6_evidence_contract",ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["company_id","supersedes_id"],["payroll_d6_evidence.company_id","payroll_d6_evidence.id"],name="fk_d6_evidence_previous",ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"],["users.id"],name="fk_d6_evidence_creator",ondelete="RESTRICT"),
        sa.CheckConstraint("revision>0",name="ck_d6_evidence_revision"),
        sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'",name="ck_d6_evidence_fingerprint"),
        sa.CheckConstraint("jsonb_typeof(payload)='object'",name="ck_d6_evidence_payload"))
    op.create_table("payroll_d6_evidence_reviews",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("company_id",sa.Integer(),nullable=False),
        sa.Column("evidence_id",sa.Integer(),nullable=False),
        sa.Column("decision",sa.String(16),nullable=False),
        sa.Column("review_reference",sa.String(500),nullable=False),
        sa.Column("reviewed_by",sa.Integer(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint("company_id","evidence_id",name="uq_d6_review_evidence"),
        sa.ForeignKeyConstraint(["company_id","evidence_id"],["payroll_d6_evidence.company_id","payroll_d6_evidence.id"],name="fk_d6_review_evidence",ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by"],["users.id"],name="fk_d6_review_reviewer",ondelete="RESTRICT"),
        sa.CheckConstraint("decision IN ('approved','rejected')",name="ck_d6_review_decision"),
        sa.CheckConstraint("length(trim(review_reference))>=10",name="ck_d6_review_reference"))
    op.execute("CREATE FUNCTION erp_d6_evidence_guard() RETURNS trigger LANGUAGE plpgsql AS $$\nDECLARE prior_id integer; actor_id integer; source payroll_d6_evidence; \nBEGIN\n IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'D6 evidence and reviews are immutable'; END IF;\n PERFORM id FROM companies WHERE id=NEW.company_id FOR UPDATE;\n actor_id := CASE WHEN TG_TABLE_NAME='payroll_d6_evidence_reviews' THEN (to_jsonb(NEW)->>'reviewed_by')::integer ELSE (to_jsonb(NEW)->>'created_by')::integer END;\n IF NOT EXISTS(SELECT 1 FROM users u JOIN user_company_roles ur ON ur.user_id=u.id JOIN role_permissions rp ON rp.role_id=ur.role_id JOIN permissions p ON p.id=rp.permission_id\n  WHERE u.id=actor_id AND u.is_active AND ur.company_id=NEW.company_id AND ur.is_active AND p.name='employees.manage') THEN\n  RAISE EXCEPTION 'D6 actor lacks active company permission';\n END IF;\n IF TG_TABLE_NAME='payroll_d6_evidence_reviews' THEN\n  SELECT * INTO source FROM payroll_d6_evidence WHERE company_id=NEW.company_id AND id=NEW.evidence_id;\n  IF source.id IS NULL OR source.created_by=NEW.reviewed_by THEN RAISE EXCEPTION 'Independent D6 reviewer required'; END IF;\n  IF NEW.decision='approved' THEN\n   SELECT e.id INTO prior_id FROM payroll_d6_evidence e JOIN payroll_d6_evidence_reviews r ON r.company_id=e.company_id AND r.evidence_id=e.id\n    WHERE e.company_id=source.company_id AND e.payroll_period_id=source.payroll_period_id AND e.employment_contract_id=source.employment_contract_id AND r.decision='approved'\n    ORDER BY e.revision DESC LIMIT 1;\n   IF source.supersedes_id IS DISTINCT FROM prior_id THEN RAISE EXCEPTION 'Stale D6 correction'; END IF;\n  END IF;\n ELSE\n  IF (NEW.payload->>'payroll_period_id')::integer IS DISTINCT FROM NEW.payroll_period_id\n    OR (NEW.payload->>'employment_contract_id')::integer IS DISTINCT FROM NEW.employment_contract_id\n    OR (NEW.payload->>'supersedes_id')::integer IS DISTINCT FROM NEW.supersedes_id\n    OR jsonb_typeof(NEW.payload->'applies') IS DISTINCT FROM 'boolean'\n    OR jsonb_typeof(NEW.payload->'intervals') IS DISTINCT FROM 'array' THEN\n    RAISE EXCEPTION 'Invalid D6 factual payload identity';\n  END IF;\n  IF (NEW.payload->>'applies')::boolean IS DISTINCT FROM (jsonb_array_length(NEW.payload->'intervals')>0) THEN\n    RAISE EXCEPTION 'D6 applicability and intervals disagree';\n  END IF;\n  IF NEW.revision<>(SELECT COALESCE(MAX(e.revision),0)+1 FROM payroll_d6_evidence e WHERE e.company_id=NEW.company_id AND e.payroll_period_id=NEW.payroll_period_id AND e.employment_contract_id=NEW.employment_contract_id) THEN\n    RAISE EXCEPTION 'D6 revision sequence mismatch';\n  END IF;\n  IF NEW.supersedes_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM payroll_d6_evidence e JOIN payroll_d6_evidence_reviews r ON r.company_id=e.company_id AND r.evidence_id=e.id\n   WHERE e.id=NEW.supersedes_id AND e.company_id=NEW.company_id AND e.payroll_period_id=NEW.payroll_period_id AND e.employment_contract_id=NEW.employment_contract_id AND e.revision<NEW.revision AND r.decision='approved') THEN\n    RAISE EXCEPTION 'Invalid D6 correction predecessor';\n  END IF;\n END IF;\n RETURN NEW;\nEND $$")
    for table in ("payroll_d6_evidence","payroll_d6_evidence_reviews"):
        op.execute(f"CREATE TRIGGER d6_evidence_write BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION erp_d6_evidence_guard()")
        op.execute(f"CREATE TRIGGER d6_evidence_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION erp_d6_evidence_guard()")


def downgrade():
    raise RuntimeError("D6 evidence is immutable; use a reviewed forward migration")
