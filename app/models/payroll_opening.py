"""Imported payroll balances and historical inputs; no duplicate GL entries."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollOpeningPackage(Base):
    __tablename__='payroll_opening_packages'
    __table_args__=(
        UniqueConstraint('company_id','id',name='uq_payroll_opening_package_company_id'),
        UniqueConstraint('company_id','opening_balance_id',name='uq_payroll_opening_package_source'),
        UniqueConstraint('company_id','request_key',name='uq_payroll_opening_package_request'),
        ForeignKeyConstraint(['company_id','opening_balance_id'],['opening_balances.company_id','opening_balances.id'],name='fk_payroll_opening_package_source',ondelete='RESTRICT'),
    )
    id: Mapped[int]=mapped_column(primary_key=True)
    company_id: Mapped[int]=mapped_column(ForeignKey('companies.id',ondelete='RESTRICT'),nullable=False)
    opening_balance_id: Mapped[int]=mapped_column(Integer,nullable=False)
    request_key: Mapped[str]=mapped_column(String(200),nullable=False)
    request_fingerprint: Mapped[str]=mapped_column(String(64),nullable=False)
    source_reference: Mapped[str]=mapped_column(String(500),nullable=False)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)


class PayrollOpeningDebt(Base):
    __tablename__='payroll_opening_debts'
    __table_args__=(
        UniqueConstraint('company_id','id',name='uq_payroll_opening_debt_company_id'),
        UniqueConstraint('company_id','employee_id',name='uq_payroll_opening_debt_employee'),
        ForeignKeyConstraint(['company_id','package_id'],['payroll_opening_packages.company_id','payroll_opening_packages.id'],name='fk_payroll_opening_debt_package',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','employee_id'],['employees.company_id','employees.id'],name='fk_payroll_opening_debt_employee',ondelete='RESTRICT'),
        CheckConstraint("net_amount <> 0 AND currency_code = 'UAH'",name='ck_payroll_opening_debt_amount'),
    )
    id: Mapped[int]=mapped_column(primary_key=True)
    company_id: Mapped[int]=mapped_column(Integer,nullable=False)
    package_id: Mapped[int]=mapped_column(Integer,nullable=False)
    employee_id: Mapped[int]=mapped_column(Integer,nullable=False)
    net_amount: Mapped[Decimal]=mapped_column(Numeric(18,2),nullable=False)
    currency_code: Mapped[str]=mapped_column(String(3),nullable=False)
    as_of: Mapped[date]=mapped_column(Date,nullable=False)


class PayrollEarningsHistory(Base):
    __tablename__='payroll_earnings_history'
    __table_args__=(
        UniqueConstraint('company_id','id',name='uq_payroll_earnings_history_company_id'),
        UniqueConstraint('company_id','employment_contract_id','month',name='uq_payroll_earnings_history_month'),
        ForeignKeyConstraint(['company_id','employment_contract_id'],['employment_contracts.company_id','employment_contracts.id'],name='fk_payroll_earnings_history_contract',ondelete='RESTRICT'),
        CheckConstraint("extract(day from month) = 1 AND month < cutover_date",name='ck_payroll_earnings_history_month'),
        CheckConstraint('gross_amount >= 0 AND vacation_earnings >= 0 AND sick_earnings >= 0',name='ck_payroll_earnings_history_amounts'),
        CheckConstraint('vacation_days BETWEEN 0 AND 31 AND sick_days BETWEEN 0 AND 31',name='ck_payroll_earnings_history_days'),
    )
    id: Mapped[int]=mapped_column(primary_key=True)
    company_id: Mapped[int]=mapped_column(Integer,nullable=False)
    employment_contract_id: Mapped[int]=mapped_column(Integer,nullable=False)
    month: Mapped[date]=mapped_column(Date,nullable=False)
    cutover_date: Mapped[date]=mapped_column(Date,nullable=False)
    gross_amount: Mapped[Decimal]=mapped_column(Numeric(18,2),nullable=False)
    vacation_earnings: Mapped[Decimal]=mapped_column(Numeric(18,2),nullable=False)
    sick_earnings: Mapped[Decimal]=mapped_column(Numeric(18,2),nullable=False)
    vacation_days: Mapped[int]=mapped_column(Integer,nullable=False)
    sick_days: Mapped[int]=mapped_column(Integer,nullable=False)
    source_reference: Mapped[str]=mapped_column(String(500),nullable=False)
    request_fingerprint: Mapped[str]=mapped_column(String(64),nullable=False)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)


class PayrollLeaveOpening(Base):
    __tablename__='payroll_leave_openings'
    __table_args__=(
        UniqueConstraint('company_id','id',name='uq_payroll_leave_opening_company_id'),
        UniqueConstraint('company_id','employment_contract_id','leave_type','working_year_start',name='uq_payroll_leave_opening_year'),
        ForeignKeyConstraint(['company_id','employment_contract_id'],['employment_contracts.company_id','employment_contracts.id'],name='fk_payroll_leave_opening_contract',ondelete='RESTRICT'),
        CheckConstraint("leave_type IN ('annual','additional','social') AND remaining_days >= 0",name='ck_payroll_leave_opening_days'),
        CheckConstraint('working_year_end >= working_year_start AND as_of >= working_year_start',name='ck_payroll_leave_opening_dates'),
    )
    id: Mapped[int]=mapped_column(primary_key=True)
    company_id: Mapped[int]=mapped_column(Integer,nullable=False)
    employment_contract_id: Mapped[int]=mapped_column(Integer,nullable=False)
    leave_type: Mapped[str]=mapped_column(String(20),nullable=False)
    working_year_start: Mapped[date]=mapped_column(Date,nullable=False)
    working_year_end: Mapped[date]=mapped_column(Date,nullable=False)
    as_of: Mapped[date]=mapped_column(Date,nullable=False)
    remaining_days: Mapped[Decimal]=mapped_column(Numeric(10,2),nullable=False)
    source_reference: Mapped[str]=mapped_column(String(500),nullable=False)
    request_fingerprint: Mapped[str]=mapped_column(String(64),nullable=False)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
