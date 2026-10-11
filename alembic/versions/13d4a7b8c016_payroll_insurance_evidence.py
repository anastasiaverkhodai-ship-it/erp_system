"""Reviewed insurance service and attributed sick-pay assessment."""
from alembic import op
import sqlalchemy as sa
revision = '13d4a7b8c016'
down_revision = '13d4a7b8c015'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('payroll_insurance_evidence',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('company_id',sa.Integer(),nullable=False),
        sa.Column('employee_id',sa.Integer(),nullable=False),
        sa.Column('case_start',sa.Date(),nullable=False),
        sa.Column('total_service_months',sa.Integer(),nullable=False),
        sa.Column('service_days',sa.Integer(),nullable=False),
        sa.Column('recent_service_months',sa.Numeric(6,3),nullable=False),
        sa.Column('entitlement',sa.String(30),nullable=False),
        sa.Column('entitlement_reference',sa.String(500),nullable=True),
        sa.Column('document_reference',sa.String(500),nullable=False),
        sa.Column('issued_on',sa.Date(),nullable=False),
        sa.Column('request_key',sa.String(200),nullable=False),
        sa.Column('request_fingerprint',sa.String(64),nullable=False),
        sa.Column('status',sa.String(20),nullable=False,server_default='pending'),
        sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('reviewed_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=True),
        sa.Column('reviewed_at',sa.DateTime(timezone=True),nullable=True),
        sa.UniqueConstraint('company_id','id',name='uq_payroll_insurance_evidence_company'),
        sa.UniqueConstraint('company_id','request_key',name='uq_payroll_insurance_evidence_request'),
        sa.ForeignKeyConstraint(['company_id','employee_id'],['employees.company_id','employees.id'],name='fk_payroll_insurance_evidence_employee',ondelete='RESTRICT'),
        sa.CheckConstraint('total_service_months >= 0 AND service_days BETWEEN 0 AND 29',name='ck_payroll_insurance_service'),
        sa.CheckConstraint('recent_service_months >= 0 AND recent_service_months <= 12 AND recent_service_months <= total_service_months + service_days / 30.0',name='ck_payroll_insurance_recent_service'),
        sa.CheckConstraint("entitlement IN ('standard','chornobyl_1_3','veteran','rehabilitated')",name='ck_payroll_insurance_entitlement'),
        sa.CheckConstraint("entitlement = 'standard' OR (entitlement_reference IS NOT NULL AND length(trim(entitlement_reference)) > 0)",name='ck_payroll_insurance_entitlement_ref'),
        sa.CheckConstraint("length(trim(document_reference)) > 0 AND length(trim(request_key)) > 0",name='ck_payroll_insurance_reference'),
        sa.CheckConstraint("status IN ('pending','verified','rejected')",name='ck_payroll_insurance_status'),
        sa.CheckConstraint("(status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL) OR (status IN ('verified','rejected') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND reviewed_by <> created_by)",name='ck_payroll_insurance_review'))
    op.add_column('payroll_sick_leave_calculations',sa.Column('insurance_evidence_id',sa.Integer(),nullable=True))
    op.add_column('payroll_sick_leave_calculations',sa.Column('insurance_assessment',sa.JSON(),nullable=True))
    op.create_foreign_key('fk_payroll_sick_insurance_evidence','payroll_sick_leave_calculations','payroll_insurance_evidence',['company_id','insurance_evidence_id'],['company_id','id'],ondelete='RESTRICT')
    op.create_check_constraint('ck_payroll_sick_insurance_assessment','payroll_sick_leave_calculations','(insurance_evidence_id IS NULL) = (insurance_assessment IS NULL)')


def downgrade():
    if op.get_bind().execute(sa.text('SELECT EXISTS (SELECT 1 FROM payroll_insurance_evidence)')).scalar():
        raise RuntimeError('Cannot downgrade insurance evidence with historical records')
    op.drop_constraint('ck_payroll_sick_insurance_assessment','payroll_sick_leave_calculations',type_='check')
    op.drop_constraint('fk_payroll_sick_insurance_evidence','payroll_sick_leave_calculations',type_='foreignkey')
    op.drop_column('payroll_sick_leave_calculations','insurance_assessment')
    op.drop_column('payroll_sick_leave_calculations','insurance_evidence_id')
    op.drop_table('payroll_insurance_evidence')
