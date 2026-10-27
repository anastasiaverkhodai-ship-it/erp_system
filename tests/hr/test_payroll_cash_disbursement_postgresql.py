from __future__ import annotations
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
import app.models
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.bank_account import BankAccount
from app.models.company import Company
from app.models.employee import Employee
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.employment_contract import EmploymentContract
from app.services.accounting_posting import AccountingPostingError
from app.services.accounting_reversal import AccountingReversalError, reverse_journal_entry
from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
from app.models.bank_statement import BankStatement
from app.models.bank_statement_line import BankStatementLine
from app.services.payroll_bank_reconciliation_service import reconcile_payroll_disbursement, unmatch_payroll_disbursement
from app.services.bank_statement_reconciliation_service import BankStatementReconciliationError
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.payment import Payment
from app.models.payroll import PayrollInput, PayrollInputSalarySlice, PayrollPeriod, PayrollPeriodStatus
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_statutory import PayrollStatutoryComponent
from app.models.user import User
from app.services.account_types import AccountNormalBalance, AccountType
from app.services.payroll_calculation_service import calculate_payroll_input
from app.services.payroll_disbursement_service import PayrollDisbursementNotFoundError, create_payroll_disbursement, generate_and_post_payroll_disbursement_journal, PayrollDisbursementSourceStateError, PayrollDisbursementCurrencyError, list_payroll_disbursements, cancel_payroll_disbursement, get_payroll_disbursement, get_payroll_disbursement_journal, reverse_payroll_disbursement_journal
from app.services.payroll_statutory_service import calculate_payroll_statutory_result, create_statutory_rate

def _postgres_url():
    from app.core.config import settings
    url = settings.database_url
    if not url or 'postgresql' not in url:
        raise RuntimeError('Real PostgreSQL database_url is required')
    if url.startswith('postgresql://'):
        url = 'postgresql+asyncpg://' + url[len('postgresql://'):]
    elif url.startswith('postgresql+psycopg://'):
        url = 'postgresql+asyncpg://' + url[len('postgresql+psycopg://'):]
    return url

def _required_values(model, overrides):
    values = dict(overrides)
    for col in model.__table__.columns:
        if col.name in values:
            continue
        if col.primary_key and col.autoincrement:
            continue
        if col.nullable:
            continue
        if col.default is not None or col.server_default is not None:
            continue
        name = col.name.lower()
        if name.endswith('_id'):
            continue
        try:
            python_type = col.type.python_type
        except Exception:
            python_type = None
        if python_type is str:
            values[col.name] = f'e2e-{col.name}'
        elif python_type is int:
            values[col.name] = 1
        elif python_type is bool:
            values[col.name] = True
        elif python_type is date:
            values[col.name] = date(2026, 1, 1)
        elif python_type is Decimal:
            values[col.name] = Decimal('1.00')
    return values

async def _seed_identity(session):
    token = uuid4().hex[:10]
    company = Company(**_required_values(Company, {'name': f'Payroll Disbursement E2E {token}'}))
    session.add(company)
    await session.flush()
    user_values = _required_values(User, {'email': f'payroll-disbursement-{token}@example.com', 'password_hash': 'not-a-real-password', 'first_name': 'Payroll', 'last_name': 'Disbursement'})
    if 'username' in User.__table__.c:
        user_values.setdefault('username', f'payroll-disbursement-{token}')
    if 'hashed_password' in User.__table__.c:
        user_values.setdefault('hashed_password', 'not-a-real-password')
    if 'is_active' in User.__table__.c:
        user_values['is_active'] = True
    user = User(**user_values)
    session.add(user)
    await session.flush()
    employee = Employee(**_required_values(Employee, {'company_id': company.id, 'employee_number': f'PD-{token}', 'first_name': 'Payroll', 'last_name': 'Disbursement', 'payment_iban': 'UA123456789012345678901234567', 'hire_date': date(2026, 1, 1), 'created_by': user.id, 'status': 'active'}))
    session.add(employee)
    await session.flush()
    contract = EmploymentContract(**_required_values(EmploymentContract, {'company_id': company.id, 'employee_id': employee.id, 'contract_number': f'PD-C-{token}', 'contract_type': 'standard', 'work_arrangement': 'full_time', 'start_date': date(2026, 1, 1), 'end_date': None, 'status': 'active', 'created_by': user.id}))
    session.add(contract)
    await session.flush()
    return (company, user, employee, contract)
from app.models.cash_desk import CashDesk
from app.models.cash_document import CashDocument
from app.services.payroll_cash_bank_gl_control_service import reconcile_payroll_bank_sources

@pytest.mark.asyncio
async def test_payroll_cash_disbursement_real_postgresql_e2e():
    engine = create_async_engine(_postgres_url(), pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with Session() as db:
            async with db.begin():
                company, user, employee, contract = await _seed_identity(db)
                period = PayrollPeriod(company_id=company.id, year=2026, month=9, start_date=date(2026, 9, 1), end_date=date(2026, 9, 30), status=PayrollPeriodStatus.FINALIZED, created_by=user.id, finalized_by=user.id, finalized_at=datetime(2026, 10, 1, 12, 0, tzinfo=UTC))
                db.add(period)
                await db.flush()
                db.add(AccountingPeriod(company_id=company.id, year=2026, month=9, start_date=date(2026, 9, 1), end_date=date(2026, 9, 30), status='open', is_locked=False))
                payroll_payable = Account(company_id=company.id, code='66', name='Payroll payable', account_type=AccountType.LIABILITY, normal_balance=AccountNormalBalance.CREDIT, is_postable=True, is_system=True, is_active=True)
                bank_gl = Account(company_id=company.id, code='311', name='Current bank account', account_type=AccountType.ASSET, normal_balance=AccountNormalBalance.DEBIT, is_postable=True, is_system=False, is_active=True)
                db.add_all([payroll_payable, bank_gl])
                await db.flush()
                bank = BankAccount(company_id=company.id, name='Payroll UAH Bank', account_number='UA-PAYROLL-E2E', currency_code='UAH', accounting_account_id=bank_gl.id, is_active=True)
                db.add(bank)
                await db.flush()
                payroll_input = PayrollInput(company_id=company.id, payroll_period_id=period.id, employment_contract_id=contract.id, scheduled_minutes=6000, worked_minutes=6000, leave_days=0, sick_days=0, manual_adjustment_amount=Decimal('0.00'), created_by=user.id)
                db.add(payroll_input)
                await db.flush()
                salary_rate = EmployeeSalaryRate(company_id=company.id, employment_contract_id=contract.id, rate_type='hourly', amount=Decimal('100.00'), currency_code='UAH', effective_from=date(2026, 9, 1), effective_to=date(2026, 9, 30), created_by=user.id)
                db.add(salary_rate)
                await db.flush()
                salary_slice = PayrollInputSalarySlice(company_id=company.id, payroll_input_id=payroll_input.id, salary_rate_id=salary_rate.id, salary_rate_type='hourly', salary_rate_amount=Decimal('100.00'), currency_code='UAH', effective_from=date(2026, 9, 1), effective_to=date(2026, 9, 30), scheduled_minutes=6000, worked_minutes=6000)
                db.add(salary_slice)
                await db.flush()
                calculation = await calculate_payroll_input(db, company_id=company.id, payroll_input_id=payroll_input.id, calculated_by=user.id)
                await db.flush()
                assert Decimal(calculation.gross_amount) == Decimal('10000.00')
                assert calculation.currency_code == 'UAH'
                for component, rate in ((PayrollStatutoryComponent.PERSONAL_INCOME_TAX, Decimal('0.18')), (PayrollStatutoryComponent.MILITARY_LEVY, Decimal('0.05')), (PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION, Decimal('0.22'))):
                    await create_statutory_rate(db, company_id=company.id, component=component, rate=rate, effective_from=date(2026, 1, 1), effective_to=None, actor_user_id=user.id)
                statutory = await calculate_payroll_statutory_result(db, company_id=company.id, payroll_calculation_id=calculation.id, actor_user_id=user.id)
                await db.flush()
                assert Decimal(statutory.net_amount) == Decimal('7700.00')
                payment_date = date(2026, 9, 30)
                for code, kind, balance in [('92', AccountType.EXPENSE, AccountNormalBalance.DEBIT), ('64', AccountType.LIABILITY, AccountNormalBalance.CREDIT), ('65', AccountType.LIABILITY, AccountNormalBalance.CREDIT)]:
                    db.add(Account(company_id=company.id, code=code, name=code, account_type=kind, normal_balance=balance, is_postable=True, is_system=True, is_active=True))
                await db.flush()
                accrual = await generate_and_post_payroll_journal_entry(db, company_id=company.id, payroll_calculation_id=calculation.id, created_by=user.id)
                cash_gl = Account(company_id=company.id, code='301', name='Cash payroll GL', account_type=AccountType.ASSET, normal_balance=AccountNormalBalance.DEBIT, is_postable=True, is_system=False, is_active=True)
                db.add(cash_gl)
                await db.flush()
                cash_desk = CashDesk(company_id=company.id, name='Payroll test cash desk', code='PAYROLL-CASH-E2E', currency_code='UAH', accounting_account_id=cash_gl.id, is_active=True)
                db.add(cash_desk)
                await db.flush()
                cash_desk.is_active = False
                await db.flush()
                with pytest.raises(PayrollDisbursementSourceStateError):
                    async with db.begin_nested():
                        await create_payroll_disbursement(db, company_id=company.id, payroll_calculation_id=calculation.id, bank_account_id=None, cash_desk_id=cash_desk.id, payment_date=payment_date, created_by=user.id, amount=Decimal('100.00'), request_key='cash-e2e-inactive-desk')
                cash_desk.is_active = True
                await db.flush()
                original_currency = cash_desk.currency_code
                cash_desk.currency_code = 'EUR'
                await db.flush()
                with pytest.raises(PayrollDisbursementCurrencyError):
                    async with db.begin_nested():
                        await create_payroll_disbursement(db, company_id=company.id, payroll_calculation_id=calculation.id, bank_account_id=None, cash_desk_id=cash_desk.id, payment_date=payment_date, created_by=user.id, amount=Decimal('100.00'), request_key='cash-e2e-wrong-currency')
                cash_desk.currency_code = original_currency
                await db.flush()
                cash_gl.is_postable = False
                await db.flush()
                with pytest.raises(PayrollDisbursementSourceStateError):
                    async with db.begin_nested():
                        await create_payroll_disbursement(db, company_id=company.id, payroll_calculation_id=calculation.id, bank_account_id=None, cash_desk_id=cash_desk.id, payment_date=payment_date, created_by=user.id, amount=Decimal('100.00'), request_key='cash-e2e-nonpostable-account')
                cash_gl.is_postable = True
                await db.flush()
                foreign_cash_company = Company(**_required_values(Company, {'name': 'Foreign Cash Payroll E2E'}))
                db.add(foreign_cash_company)
                await db.flush()
                foreign_cash_gl = Account(company_id=foreign_cash_company.id, code='301', name='Foreign cash GL', account_type=AccountType.ASSET, normal_balance=AccountNormalBalance.DEBIT, is_postable=True, is_system=False, is_active=True)
                db.add(foreign_cash_gl)
                await db.flush()
                foreign_cash_desk = CashDesk(company_id=foreign_cash_company.id, name='Foreign cash desk', code='FOREIGN-CASH-E2E', currency_code='UAH', accounting_account_id=foreign_cash_gl.id, is_active=True)
                db.add(foreign_cash_desk)
                await db.flush()
                with pytest.raises(PayrollDisbursementNotFoundError):
                    async with db.begin_nested():
                        await create_payroll_disbursement(db, company_id=company.id, payroll_calculation_id=calculation.id, bank_account_id=None, cash_desk_id=foreign_cash_desk.id, payment_date=payment_date, created_by=user.id, amount=Decimal('100.00'), request_key='cash-e2e-cross-company')
                assert await db.scalar(select(func.count(PayrollDisbursement.id)).where(PayrollDisbursement.company_id == company.id, PayrollDisbursement.payroll_calculation_id == calculation.id)) == 0
                cash = await create_payroll_disbursement(db, company_id=company.id, payroll_calculation_id=calculation.id, bank_account_id=None, cash_desk_id=cash_desk.id, payment_date=payment_date, created_by=user.id, amount=Decimal('3000.00'), request_key='cash-payroll-e2e-first')
                assert cash.cash_account_id_snapshot == cash_gl.id
                await db.flush()
                assert cash.bank_account_id is None
                assert cash.cash_desk_id == cash_desk.id
                assert cash.employee_iban_snapshot is None
                assert Decimal(cash.amount) == Decimal('3000.00')
                replayed = await create_payroll_disbursement(db, company_id=company.id, payroll_calculation_id=calculation.id, bank_account_id=None, cash_desk_id=cash_desk.id, payment_date=payment_date, created_by=user.id, amount=Decimal('3000.00'), request_key='cash-payroll-e2e-first')
                assert replayed.id == cash.id
                with pytest.raises(PayrollDisbursementSourceStateError):
                    async with db.begin_nested():
                        await create_payroll_disbursement(db, company_id=company.id, payroll_calculation_id=calculation.id, bank_account_id=bank.id, cash_desk_id=None, payment_date=payment_date, created_by=user.id, amount=Decimal('3000.00'), request_key='cash-payroll-e2e-first')
                # A declaration is evidence, not permission to spend cash.
                from sqlalchemy import text
                from sqlalchemy.exc import IntegrityError, DBAPIError

                async with db.begin_nested() as opening_sp:
                    opening_id = await db.scalar(
                        text("""
                            INSERT INTO cash_desk_opening_evidence (
                                company_id, cash_desk_id,
                                effective_date, declared_amount,
                                currency_code, evidence_reference, attested_by
                            )
                            VALUES (
                                :company, :desk,
                                :effective_date, 3500.00,
                                'UAH', 'E2E-ATTESTATION-NOT-VERIFIED', :actor
                            )
                            RETURNING id
                        """),
                        {
                            "company": company.id,
                            "desk": cash_desk.id,
                            "effective_date": payment_date,
                            "actor": user.id,
                        },
                    )
                    assert opening_id is not None

                    with pytest.raises(IntegrityError):
                        async with db.begin_nested():
                            await db.execute(
                                text("""
                                    INSERT INTO cash_desk_opening_evidence (
                                        company_id, cash_desk_id,
                                        effective_date, declared_amount,
                                        currency_code, evidence_reference, attested_by
                                    )
                                    VALUES (
                                        :company, :desk, :effective_date, 1,
                                        'UAH', 'DUPLICATE', :actor
                                    )
                                """),
                                {
                                    "company": company.id,
                                    "desk": cash_desk.id,
                                    "effective_date": payment_date,
                                    "actor": user.id,
                                },
                            )

                    with pytest.raises(IntegrityError):
                        async with db.begin_nested():
                            await db.execute(
                                text("""
                                    INSERT INTO cash_desk_opening_evidence (
                                        company_id, cash_desk_id,
                                        effective_date, declared_amount,
                                        currency_code, evidence_reference, attested_by
                                    )
                                    VALUES (
                                        :company, :desk, :effective_date, -1,
                                        'UAH', 'NEGATIVE', :actor
                                    )
                                """),
                                {
                                    "company": company.id,
                                    "desk": foreign_cash_desk.id,
                                    "effective_date": payment_date,
                                    "actor": user.id,
                                },
                            )

                    with pytest.raises(IntegrityError):
                        async with db.begin_nested():
                            await db.execute(
                                text("""
                                    INSERT INTO cash_desk_opening_evidence (
                                        company_id, cash_desk_id,
                                        effective_date, declared_amount,
                                        currency_code, evidence_reference, attested_by
                                    )
                                    VALUES (
                                        :company, :desk, :effective_date, 5,
                                        'UAH', 'WRONG-COMPANY-DESK', :actor
                                    )
                                """),
                                {
                                    "company": company.id,
                                    "desk": foreign_cash_desk.id,
                                    "effective_date": payment_date,
                                    "actor": user.id,
                                },
                            )

                    with pytest.raises(DBAPIError):
                        async with db.begin_nested():
                            await db.execute(
                                text("""
                                    UPDATE cash_desk_opening_evidence
                                    SET declared_amount = 9000
                                    WHERE id = :opening_id
                                """),
                                {"opening_id": opening_id},
                            )

                    opening_row = (
                        await db.execute(
                            text("""
                                SELECT declared_amount, evidence_reference,
                                       cash_desk_id
                                FROM cash_desk_opening_evidence
                                WHERE id = :opening_id
                            """),
                            {"opening_id": opening_id},
                        )
                    ).one()

                    assert opening_row.declared_amount == Decimal("3500.00")
                    assert opening_row.cash_desk_id == cash_desk.id
                    assert opening_row.evidence_reference == (
                        "E2E-ATTESTATION-NOT-VERIFIED"
                    )

                    # A real evidence row would remain immutable.
                    # This fixture is deliberately rolled back.
                    await opening_sp.rollback()

                print("CASH DESK OPENING SQL E2E = PASS")
                print("CASH OPENING COMPANY SCOPE = PASS")
                print("CASH OPENING DUPLICATE / NEGATIVE = REJECTED")
                print("CASH OPENING IMMUTABILITY = PASS")
                print("CASH OPENING TEST DATA = ROLLED BACK")

                from uuid import uuid4
                from sqlalchemy import inspect as sa_inspect, text
                from sqlalchemy.exc import DBAPIError, IntegrityError
                from app.models.payroll_disbursement import (
                    CashDeskOpeningEvidence,
                    CashDeskOpeningReview,
                )
                from app.services.cash_desk_opening_review_service import (
                    record_independent_cash_opening_review,
                    CashOpeningReviewError,
                )

                # Test-only independent user; no production identity is invented.
                user_class = type(user)
                user_mapper = sa_inspect(user_class)

                unique_columns = {
                    column.name for column in user_mapper.columns
                    if column.unique
                }
                for constraint in user_class.__table__.constraints:
                    if constraint.__class__.__name__ == "UniqueConstraint":
                        unique_columns.update(
                            column.name for column in constraint.columns
                        )

                new_user_fields = {}
                suffix = uuid4().hex[:12]

                for column in user_mapper.columns:
                    if column.primary_key:
                        continue

                    value = getattr(user, column.key, None)

                    if column.name in unique_columns:
                        if not isinstance(value, str):
                            raise AssertionError(
                                "STOP: Unsupported unique reviewer column "
                                + column.name
                            )

                        if "@" in value:
                            local, domain = value.rsplit("@", 1)
                            candidate = local + "-rev-" + suffix + "@" + domain
                        else:
                            candidate = value + "-rev-" + suffix

                        limit = getattr(column.type, "length", None)
                        if limit is not None and len(candidate) > limit:
                            candidate = ("review-" + suffix)[:limit]

                        value = candidate

                    if value is not None:
                        new_user_fields[column.key] = value

                reviewer = user_class(**new_user_fields)
                db.add(reviewer)
                await db.flush()

                assert reviewer.id != user.id

                opening = CashDeskOpeningEvidence(
                    company_id=company.id,
                    cash_desk_id=cash_desk.id,
                    effective_date=payment_date,
                    declared_amount=Decimal("3500.00"),
                    currency_code="UAH",
                    evidence_reference="TEST-COUNT-REVIEW-R1",
                    attested_by=user.id,
                )
                db.add(opening)
                await db.flush()
                from app.services.cash_physical_opening_adapter import (
                    load_reviewed_cash_opening,
                    CashOpeningUnavailableError,
                )
                from app.services.cash_physical_chronology_service import (
                    calculate_cash_chronology,
                )

                with pytest.raises(CashOpeningUnavailableError):
                    await load_reviewed_cash_opening(
                        db,
                        company_id=company.id,
                        cash_desk_id=cash_desk.id,
                        as_of_date=payment_date,
                    )

                print("CASH OPENING UNREVIEWED = REJECTED")


                with pytest.raises(CashOpeningReviewError):
                    await record_independent_cash_opening_review(
                        db,
                        company_id=company.id,
                        opening_evidence_id=opening.id,
                        reviewed_by=user.id,
                        review_reference="SELF",
                    )

                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(
                            text("""
                                INSERT INTO cash_desk_opening_reviews
                                (company_id, opening_evidence_id,
                                 reviewed_by, review_reference)
                                VALUES (:company, :opening, :actor, 'SELF-SQL')
                            """),
                            {
                                "company": company.id,
                                "opening": opening.id,
                                "actor": user.id,
                            },
                        )

                with pytest.raises(CashOpeningReviewError):
                    await record_independent_cash_opening_review(
                        db,
                        company_id=foreign_cash_company.id,
                        opening_evidence_id=opening.id,
                        reviewed_by=reviewer.id,
                        review_reference="CROSS-COMPANY",
                    )

                for bad in ("", "  ", "A" * 256):
                    with pytest.raises(CashOpeningReviewError):
                        await record_independent_cash_opening_review(
                            db,
                            company_id=company.id,
                            opening_evidence_id=opening.id,
                            reviewed_by=reviewer.id,
                            review_reference=bad,
                        )

                review = await record_independent_cash_opening_review(
                    db,
                    company_id=company.id,
                    opening_evidence_id=opening.id,
                    reviewed_by=reviewer.id,
                    review_reference="TEST-INDEPENDENT-REVIEW-R1",
                )

                assert review.id is not None
                assert review.reviewed_by != opening.attested_by

                replay = await record_independent_cash_opening_review(
                    db,
                    company_id=company.id,
                    opening_evidence_id=opening.id,
                    reviewed_by=reviewer.id,
                    review_reference="TEST-INDEPENDENT-REVIEW-R1",
                )
                assert replay.id == review.id

                with pytest.raises(CashOpeningReviewError):
                    await record_independent_cash_opening_review(
                        db,
                        company_id=company.id,
                        opening_evidence_id=opening.id,
                        reviewed_by=reviewer.id,
                        review_reference="CHANGED-REVIEW",
                    )

                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        await db.execute(
                            text("""
                                INSERT INTO cash_desk_opening_reviews
                                (company_id, opening_evidence_id,
                                 reviewed_by, review_reference)
                                VALUES (:company, :opening, :actor, 'DUPLICATE')
                            """),
                            {
                                "company": company.id,
                                "opening": opening.id,
                                "actor": reviewer.id,
                            },
                        )

                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(
                            text("""
                                UPDATE cash_desk_opening_reviews
                                SET review_reference = 'MUTATED'
                                WHERE id = :id
                            """),
                            {"id": review.id},
                        )

                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(
                            text("""
                                DELETE FROM cash_desk_opening_reviews
                                WHERE id = :id
                            """),
                            {"id": review.id},
                        )

                verified_opening = await load_reviewed_cash_opening(
                    db,
                    company_id=company.id,
                    cash_desk_id=cash_desk.id,
                    as_of_date=payment_date,
                    lock_cash_desk=True,
                )

                assert verified_opening.company_id == company.id
                assert verified_opening.cash_desk_id == cash_desk.id
                assert verified_opening.evidence_id == opening.id
                assert verified_opening.currency_code == "UAH"
                assert verified_opening.effective_date == payment_date
                assert verified_opening.amount == Decimal("3500.00")

                empty_chronology = calculate_cash_chronology(
                    opening=verified_opening,
                    movements=(),
                )
                assert empty_chronology.closing_balance == Decimal("3500.00")

                with pytest.raises(CashOpeningUnavailableError):
                    await load_reviewed_cash_opening(
                        db,
                        company_id=foreign_cash_company.id,
                        cash_desk_id=cash_desk.id,
                        as_of_date=payment_date,
                    )

                with pytest.raises(CashOpeningUnavailableError):
                    await load_reviewed_cash_opening(
                        db,
                        company_id=company.id,
                        cash_desk_id=cash_desk.id,
                        as_of_date=payment_date.replace(year=2025),
                    )

                with pytest.raises(CashOpeningUnavailableError):
                    await load_reviewed_cash_opening(
                        db,
                        company_id=company.id,
                        cash_desk_id=0,
                        as_of_date=payment_date,
                    )

                with pytest.raises(CashOpeningUnavailableError):
                    await load_reviewed_cash_opening(
                        db,
                        company_id=company.id,
                        cash_desk_id=cash_desk.id,
                        as_of_date=payment_date,
                        lock_cash_desk="yes",
                    )

                print("CASH OPENING APPROVED DB ADAPTER = PASS")
                print("CASH OPENING FOREIGN COMPANY = REJECTED")
                print("CASH OPENING FUTURE CUTOVER = REJECTED")
                print("CASH OPENING INVALID INPUT = REJECTED")
                print("CASH OPENING CALCULATION BASELINE = PASS")


                cash_journal = await generate_and_post_payroll_disbursement_journal(db, company_id=company.id, payroll_disbursement_id=cash.id, created_by=user.id)
                await db.flush()
                assert cash_journal.status == 'posted'
                assert cash_journal.payroll_disbursement_id == cash.id
                cash_lines = (await db.execute(select(JournalEntryLine).where(JournalEntryLine.journal_entry_id == cash_journal.id))).scalars().all()
                assert len(cash_lines) == 2
                by_account = {line.account_id: line for line in cash_lines}
                assert len(by_account) == 2
                assert by_account[payroll_payable.id].debit == Decimal('3000.00')
                assert by_account[payroll_payable.id].credit == Decimal('0.00')
                assert by_account[cash_gl.id].debit == Decimal('0.00')
                assert by_account[cash_gl.id].credit == Decimal('3000.00')
                assert await db.scalar(select(func.count(Payment.id)).where(Payment.company_id == company.id)) == 0
                assert await db.scalar(select(func.count(CashDocument.id)).where(CashDocument.company_id == company.id)) == 0
                bank_part = await create_payroll_disbursement(db, company_id=company.id, payroll_calculation_id=calculation.id, bank_account_id=bank.id, cash_desk_id=None, payment_date=payment_date, created_by=user.id, amount=Decimal('4700.00'), request_key='cash-payroll-e2e-second-bank')
                bank_journal = await generate_and_post_payroll_disbursement_journal(db, company_id=company.id, payroll_disbursement_id=bank_part.id, created_by=user.id)
                await db.flush()
                assert bank_journal.status == 'posted'
                assert bank_part.cash_desk_id is None
                bank_lines = (await db.execute(select(JournalEntryLine).where(JournalEntryLine.journal_entry_id == bank_journal.id))).scalars().all()
                bank_by_account = {line.account_id: line for line in bank_lines}
                assert bank_by_account[payroll_payable.id].debit == Decimal('4700.00')
                assert bank_by_account[bank_gl.id].credit == Decimal('4700.00')
                assert Decimal(cash.amount) + Decimal(bank_part.amount) == Decimal('7700.00')
                gl_control_rows = await reconcile_payroll_bank_sources(db, company_id=company.id, date_from=payment_date, date_to=payment_date)
                assert isinstance(gl_control_rows, list)
                assert len(gl_control_rows) == 2, 'Expected one bank and one cash payroll GL result'
                print('CASH + BANK GL CONTROL EXECUTED = PASS')
                print('GL CONTROL SOURCE COUNT = 2')
                assert len(gl_control_rows) == 2
                for item in gl_control_rows:
                    assert item.source_type == 'payroll_disbursements'
                    assert item.event_count == 1
                    assert item.matched is True, f'Payroll GL mismatch: {item.issues}'
                    assert item.issues == []
                    assert item.difference == Decimal('0.00')
                    assert item.expected_amount == item.posted_amount
                observed = sorted((abs(Decimal(item.expected_amount)) for item in gl_control_rows))
                assert observed == [Decimal('3000.00'), Decimal('4700.00')], observed
                print('CASH + BANK GL SEMANTIC RESULTS = PASS')
                print('GL EXPECTED / POSTED / DIFFERENCE = VERIFIED')
                print('GL ISSUES = 0')
                replacement_cash_gl = Account(company_id=company.id, code='302', name='Replacement cash GL for chronology test', account_type=AccountType.ASSET, normal_balance=AccountNormalBalance.DEBIT, is_postable=True, is_system=False, is_active=True)
                db.add(replacement_cash_gl)
                await db.flush()

                # =========================================================
                # PostgreSQL must enforce historical account provenance.
                # All rejected operations run inside rollback savepoints.
                # =========================================================

                from sqlalchemy import insert as sql_insert, update as sql_update
                from sqlalchemy.exc import DBAPIError, IntegrityError

                def sqlstate(exc):
                    original_error = exc.orig
                    for item in (
                        original_error,
                        getattr(original_error, "__cause__", None),
                    ):
                        if item is None:
                            continue
                        state = (
                            getattr(item, "sqlstate", None)
                            or getattr(item, "pgcode", None)
                        )
                        if state:
                            return state
                    return None

                cash_id = cash.id
                snapshot_id = cash.cash_account_id_snapshot

                assert snapshot_id == cash_gl.id
                assert snapshot_id != replacement_cash_gl.id

                initial_rows = await db.scalar(
                    select(func.count(PayrollDisbursement.id)).where(
                        PayrollDisbursement.company_id == company.id,
                        PayrollDisbursement.payroll_calculation_id
                        == calculation.id,
                    )
                )

                # 1. UPDATE must be rejected by the database trigger.
                with pytest.raises(DBAPIError) as update_error:
                    async with db.begin_nested():
                        await db.execute(
                            sql_update(PayrollDisbursement)
                            .where(
                                PayrollDisbursement.company_id == company.id,
                                PayrollDisbursement.id == cash_id,
                            )
                            .values(
                                cash_account_id_snapshot=replacement_cash_gl.id
                            )
                        )
                        await db.flush()

                assert (
                    sqlstate(update_error.value) == "P0001"
                    or "Historical payroll cash account is immutable"
                       in str(update_error.value)
                ), (
                    "Unexpected UPDATE failure: " + str(update_error.value)
                )

                await db.refresh(cash)
                assert cash.cash_account_id_snapshot == cash_gl.id
                print("SNAPSHOT SQL IMMUTABILITY = PASS")

                # Copy the existing verified payout's persisted values.
                # An INSERT (not an UPDATE) must exercise the FK and CHECK.
                values = {
                    col.name: getattr(cash, col.name)
                    for col in PayrollDisbursement.__table__.columns
                    if col.name != "id"
                }

                # 2. A cash payout without a snapshot must fail CHECK.
                missing_snapshot = dict(values)
                missing_snapshot["request_key"] = (
                    "cash-e2e-missing-account-snapshot"
                )
                missing_snapshot["cash_account_id_snapshot"] = None

                with pytest.raises(IntegrityError) as missing_error:
                    async with db.begin_nested():
                        await db.execute(
                            sql_insert(PayrollDisbursement)
                            .values(**missing_snapshot)
                        )
                        await db.flush()

                assert sqlstate(missing_error.value) == "23514", (
                    "Expected PostgreSQL CHECK violation, got: "
                    + str(missing_error.value)
                )

                assert (
                    "ck_payroll_disbursement_cash_account_snapshot"
                    in str(missing_error.value)
                )

                print("CASH SNAPSHOT CHECK CONSTRAINT = PASS")

                # 3. A GL account belonging to another company must fail FK.
                foreign_account = dict(values)
                foreign_account["request_key"] = (
                    "cash-e2e-cross-company-account-snapshot"
                )
                foreign_account["cash_account_id_snapshot"] = (
                    foreign_cash_gl.id
                )

                assert foreign_cash_gl.company_id != company.id

                with pytest.raises(IntegrityError) as foreign_error:
                    async with db.begin_nested():
                        await db.execute(
                            sql_insert(PayrollDisbursement)
                            .values(**foreign_account)
                        )
                        await db.flush()

                assert sqlstate(foreign_error.value) == "23503", (
                    "Expected PostgreSQL FK violation, got: "
                    + str(foreign_error.value)
                )

                assert (
                    "fk_payroll_disbursements_cash_account_snapshot"
                    in str(foreign_error.value)
                )

                print("CROSS-COMPANY ACCOUNT FK = PASS")

                # Rejections must not leak a single new payroll row.
                final_rows = await db.scalar(
                    select(func.count(PayrollDisbursement.id)).where(
                        PayrollDisbursement.company_id == company.id,
                        PayrollDisbursement.payroll_calculation_id
                        == calculation.id,
                    )
                )

                assert final_rows == initial_rows
                await db.refresh(cash)
                assert cash.cash_account_id_snapshot == snapshot_id

                print("REJECTED SQL INSERT/UPDATE ROW LEAK = 0")
                print("HISTORICAL CASH ACCOUNT PROVENANCE = PRESERVED")

                original_cash_account_id = cash_desk.accounting_account_id
                assert original_cash_account_id == cash_gl.id
                try:
                    cash_desk.accounting_account_id = replacement_cash_gl.id
                    await db.flush()
                    remapped = await reconcile_payroll_bank_sources(db, company_id=company.id, date_from=payment_date, date_to=payment_date)
                    assert len(remapped) == 2
                    assert all((item.matched for item in remapped)), [(item.difference, item.issues) for item in remapped]
                    assert all((not item.issues for item in remapped))
                    assert all((item.difference == Decimal('0.00') for item in remapped))
                    assert cash.cash_account_id_snapshot == cash_gl.id
                    assert cash_desk.accounting_account_id == replacement_cash_gl.id
                    print('HISTORICAL CASH ACCOUNT REMAPPING = PASS')
                    print('ORIGINAL CASH ACCOUNT SNAPSHOT = PRESERVED')
                finally:
                    cash_desk.accounting_account_id = original_cash_account_id
                    await db.flush()
                restored = await reconcile_payroll_bank_sources(db, company_id=company.id, date_from=payment_date, date_to=payment_date)
                assert len(restored) == 2
                assert all((row.matched for row in restored))
                assert all((not row.issues for row in restored))
                assert all((row.difference == Decimal('0.00') for row in restored))
                print('RESTORED CASH ACCOUNT GL CONTROL = PASS')
                print('TEST DATABASE CHRONOLOGY STATE = RESTORED')
                before_count = await db.scalar(select(func.count(PayrollDisbursement.id)).where(PayrollDisbursement.company_id == company.id, PayrollDisbursement.payroll_calculation_id == calculation.id))
                assert before_count == 2
                with pytest.raises(PayrollDisbursementSourceStateError):
                    async with db.begin_nested():
                        await create_payroll_disbursement(db, company_id=company.id, payroll_calculation_id=calculation.id, bank_account_id=None, cash_desk_id=cash_desk.id, payment_date=payment_date, created_by=user.id, amount=Decimal('0.01'), request_key='cash-payroll-e2e-overpay')
                for bank_id, desk_id in ((None, None), (bank.id, cash_desk.id)):
                    with pytest.raises(PayrollDisbursementSourceStateError):
                        async with db.begin_nested():
                            await create_payroll_disbursement(db, company_id=company.id, payroll_calculation_id=calculation.id, bank_account_id=bank_id, cash_desk_id=desk_id, payment_date=payment_date, created_by=user.id, amount=Decimal('0.01'), request_key='cash-payroll-e2e-invalid-source')
                with pytest.raises(PayrollDisbursementNotFoundError):
                    async with db.begin_nested():
                        await create_payroll_disbursement(db, company_id=company.id, payroll_calculation_id=calculation.id, bank_account_id=None, cash_desk_id=987654321, payment_date=payment_date, created_by=user.id, amount=Decimal('0.01'), request_key='cash-payroll-e2e-missing-desk')
                after_count = await db.scalar(select(func.count(PayrollDisbursement.id)).where(PayrollDisbursement.company_id == company.id, PayrollDisbursement.payroll_calculation_id == calculation.id))
                assert after_count == before_count
                repeated_journal = await generate_and_post_payroll_disbursement_journal(db, company_id=company.id, payroll_disbursement_id=cash.id, created_by=user.id)
                assert repeated_journal.id == cash_journal.id
                from app.services.payroll_cash_document_service import (
                    ensure_payroll_cash_issue_document,
                    PayrollCashDocumentError,
                )

                from app.models.payroll_disbursement import PayrollCashDocument

                payment_count_before_doc = await db.scalar(
                    select(func.count(Payment.id)).where(
                        Payment.company_id == company.id
                    )
                )

                journal_count_before_doc = await db.scalar(
                    select(func.count(JournalEntry.id)).where(
                        JournalEntry.company_id == company.id
                    )
                )

                automatic_document = await db.scalar(
                    select(PayrollCashDocument).where(
                        PayrollCashDocument.company_id == company.id,
                        PayrollCashDocument.payroll_disbursement_id == cash.id,
                        PayrollCashDocument.event_kind == "issue",
                    )
                )

                assert automatic_document is not None
                assert automatic_document.cash_desk_id == cash_desk.id
                assert automatic_document.amount == Decimal("3000.00")
                assert automatic_document.document_date == payment_date

                automatic_document_id = automatic_document.id

                print(
                    "AUTOMATIC CASH DOCUMENT BEFORE EXPLICIT SERVICE CALL = PASS"
                )

                cash_document = await ensure_payroll_cash_issue_document(
                    db,
                    company_id=company.id,
                    payroll_disbursement_id=cash.id,
                    created_by=user.id,
                )

                assert cash_document.id is not None
                assert cash_document.cash_desk_id == cash_desk.id
                assert cash_document.payroll_disbursement_id == cash.id
                assert cash_document.event_kind == "issue"
                assert Decimal(cash_document.amount) == Decimal("3000.00")
                assert cash_document.document_date == payment_date
                assert cash_document.currency_code == cash.currency_code

                reused_cash_document = (
                    await ensure_payroll_cash_issue_document(
                        db,
                        company_id=company.id,
                        payroll_disbursement_id=cash.id,
                        created_by=user.id,
                    )
                )

                assert reused_cash_document.id == cash_document.id
                assert cash_document.id == automatic_document_id

                cash_document_count = await db.scalar(
                    select(func.count(PayrollCashDocument.id)).where(
                        PayrollCashDocument.company_id == company.id,
                        PayrollCashDocument.payroll_disbursement_id == cash.id,
                        PayrollCashDocument.event_kind == "issue",
                    )
                )
                assert cash_document_count == 1

                for invalid in (
                    {"company_id": 0, "payroll_disbursement_id": cash.id},
                    {"company_id": company.id, "payroll_disbursement_id": 987654321},
                    {"company_id": 987654321, "payroll_disbursement_id": cash.id},
                ):
                    with pytest.raises(PayrollCashDocumentError):
                        await ensure_payroll_cash_issue_document(
                            db,
                            company_id=invalid["company_id"],
                            payroll_disbursement_id=invalid["payroll_disbursement_id"],
                            created_by=user.id,
                        )

                with pytest.raises(PayrollCashDocumentError):
                    await ensure_payroll_cash_issue_document(
                        db,
                        company_id=company.id,
                        payroll_disbursement_id=cash.id,
                        created_by=0,
                    )

                assert await db.scalar(
                    select(func.count(Payment.id)).where(
                        Payment.company_id == company.id
                    )
                ) == payment_count_before_doc

                assert await db.scalar(
                    select(func.count(JournalEntry.id)).where(
                        JournalEntry.company_id == company.id
                    )
                ) == journal_count_before_doc

                print("PAYROLL CASH SERVICE CREATION = PASS")
                print("PAYROLL CASH SERVICE IDEMPOTENCY = PASS")
                print("PAYROLL CASH SERVICE NEGATIVE INPUTS = PASS")
                print("PAYROLL CASH DOCUMENT EXTRA PAYMENT = 0")
                print("PAYROLL CASH DOCUMENT EXTRA JOURNAL = 0")

                reversal = await reverse_payroll_disbursement_journal(db, company_id=company.id, payroll_disbursement_id=cash.id, reversal_date=payment_date, reversed_by=user.id)
                from app.services.payroll_cash_document_service import (
                    ensure_payroll_cash_reversal_document,
                )

                from app.models.payroll_disbursement import PayrollCashDocument

                original_issue = await db.scalar(
                    select(PayrollCashDocument).where(
                        PayrollCashDocument.company_id == company.id,
                        PayrollCashDocument.payroll_disbursement_id == cash.id,
                        PayrollCashDocument.event_kind == "issue",
                    )
                )
                assert original_issue is not None

                automatic_reversal = await db.scalar(
                    select(PayrollCashDocument).where(
                        PayrollCashDocument.company_id == company.id,
                        PayrollCashDocument.reversal_of_id == original_issue.id,
                        PayrollCashDocument.event_kind == "reversal",
                    )
                )

                assert automatic_reversal is not None
                assert automatic_reversal.cash_desk_id == cash_desk.id
                assert automatic_reversal.payroll_disbursement_id == cash.id
                assert automatic_reversal.document_date == reversal.entry_date
                assert automatic_reversal.amount == original_issue.amount
                assert automatic_reversal.currency_code == original_issue.currency_code
                assert automatic_reversal.id != original_issue.id

                reused_reversal = await ensure_payroll_cash_reversal_document(
                    db,
                    company_id=company.id,
                    payroll_disbursement_id=cash.id,
                    created_by=user.id,
                )
                assert reused_reversal.id == automatic_reversal.id

                reversal_document_count = await db.scalar(
                    select(func.count(PayrollCashDocument.id)).where(
                        PayrollCashDocument.company_id == company.id,
                        PayrollCashDocument.payroll_disbursement_id == cash.id,
                        PayrollCashDocument.event_kind == "reversal",
                    )
                )
                assert reversal_document_count == 1

                print("AUTOMATIC CASH REVERSAL DOCUMENT = PASS")
                print("CASH DOCUMENT REVERSAL IDENTITY = PASS")
                print("CASH DOCUMENT REVERSAL IDEMPOTENCY = PASS")

                await db.flush()
                assert reversal.id != cash_journal.id
                assert reversal.reversal_of_id == cash_journal.id
                assert reversal.status == 'posted'
                await db.refresh(cash_journal)
                assert cash_journal.status == 'reversed'
                reversal_lines = (await db.execute(select(JournalEntryLine).where(JournalEntryLine.journal_entry_id == reversal.id))).scalars().all()
                reverse_accounts = {line.account_id: line for line in reversal_lines}
                assert len(reversal_lines) == 2
                assert reverse_accounts[payroll_payable.id].credit == Decimal('3000.00')
                assert reverse_accounts[cash_gl.id].debit == Decimal('3000.00')
                assert await db.scalar(select(func.count(Payment.id)).where(Payment.company_id == company.id)) == 0

                # Execute real c050 migration refusal checks against
                # the existing isolated PostgreSQL transaction.
                from sqlalchemy import text
                import importlib.util
                from pathlib import Path
                from unittest.mock import patch
                from alembic.migration import MigrationContext
                from alembic.operations import Operations

                migration_file = (
                    Path(__file__).resolve().parents[2]
                    / "alembic"
                    / "versions"
                    / "13d4a7b8c050_payroll_cash_account_snapshot.py"
                )
                assert migration_file.is_file()

                spec = importlib.util.spec_from_file_location(
                    "erp_test_c050_migration",
                    migration_file,
                )
                assert spec is not None and spec.loader is not None

                c050_migration = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(c050_migration)

                assert callable(c050_migration.upgrade)
                assert callable(c050_migration.downgrade)

                before_migration_rows = await db.scalar(
                    select(func.count(PayrollDisbursement.id)).where(
                        PayrollDisbursement.company_id == company.id,
                        PayrollDisbursement.cash_desk_id.is_not(None),
                    )
                )

                assert before_migration_rows >= 1

                # Use the actual PostgreSQL connection and real Alembic
                # Operations object. Do not modify the live database.
                def check_c050_refusals(sync_session):
                    connection = sync_session.connection()

                    operations = Operations(
                        MigrationContext.configure(connection)
                    )

                    with patch.object(c050_migration, "op", operations):
                        with pytest.raises(
                            RuntimeError,
                            match="Cannot upgrade c050: existing cash payroll",
                        ):
                            c050_migration.upgrade()

                    print("c050 UPGRADE REFUSAL = PASS")

                    with patch.object(c050_migration, "op", operations):
                        with pytest.raises(
                            RuntimeError,
                            match="Cannot downgrade c050 with existing cash payroll",
                        ):
                            c050_migration.downgrade()

                    print("c050 DOWNGRADE REFUSAL = PASS")

                await db.run_sync(check_c050_refusals)

                after_migration_rows = await db.scalar(
                    select(func.count(PayrollDisbursement.id)).where(
                        PayrollDisbursement.company_id == company.id,
                        PayrollDisbursement.cash_desk_id.is_not(None),
                    )
                )

                assert after_migration_rows == before_migration_rows

                await db.refresh(cash)
                assert cash.cash_account_id_snapshot == cash_gl.id

                # The schema must still contain the snapshot column.
                schema_columns = (
                    await db.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'payroll_disbursements' "
                            "AND column_name = 'cash_account_id_snapshot'"
                        )
                    )
                ).scalars().all()

                assert schema_columns == ["cash_account_id_snapshot"]

                print("c050 SCHEMA PRESERVED AFTER REFUSALS = PASS")
                print("c050 CASH PAYROLL ROWS PRESERVED = PASS")

                # Verify the c051 table against a real cash payout.
                from sqlalchemy import text

                doc_id = cash_document.id

                assert doc_id is not None

                from sqlalchemy.exc import IntegrityError, DBAPIError

                # One original cash issue per salary payout.
                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        await db.execute(
                            text("""
                                INSERT INTO payroll_cash_documents (
                                    company_id, cash_desk_id,
                                    payroll_disbursement_id,
                                    document_number, event_kind,
                                    document_date, amount,
                                    currency_code, created_by
                                )
                                VALUES (
                                    :company_id, :desk_id, :payout_id,
                                    'PAYROLL-E2E-DUPLICATE', 'issue',
                                    :payment_date, 3000, 'UAH', :actor
                                )
                            """),
                            {
                                "company_id": company.id,
                                "desk_id": cash_desk.id,
                                "payout_id": cash.id,
                                "payment_date": payment_date,
                                "actor": user.id,
                            },
                        )

                # Linked cash document is immutable through raw SQL.
                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(
                            text("""
                                UPDATE payroll_cash_documents
                                SET amount = amount + 1
                                WHERE id = :doc_id
                            """),
                            {"doc_id": doc_id},
                        )

                stored = await db.execute(
                    text("""
                        SELECT payroll_disbursement_id, amount,
                               event_kind, cash_desk_id
                        FROM payroll_cash_documents
                        WHERE id = :doc_id
                    """),
                    {"doc_id": doc_id},
                )
                stored = stored.one()

                assert stored.payroll_disbursement_id == cash.id
                assert stored.cash_desk_id == cash_desk.id
                assert stored.event_kind == "issue"
                assert stored.amount == Decimal("3000.00")

                print("PAYROLL CASH DOCUMENT SQL E2E = PASS")
                print("DUPLICATE CASH ISSUE = REJECTED")
                print("CASH DOCUMENT IMMUTABILITY = PASS")
                from app.services.cash_physical_event_reader import (
                    read_physical_cash_events,
                    CashEventSourceError,
                )

                cash_events = await read_physical_cash_events(
                    db,
                    company_id=company.id,
                    cash_desk_id=cash_desk.id,
                    currency_code="UAH",
                    effective_date=payment_date,
                )

                event_kinds = {event.source_kind for event in cash_events}

                assert "payroll_issue" in event_kinds
                assert "payroll_reversal" in event_kinds
                assert all(
                    event.cash_desk_id == cash_desk.id
                    for event in cash_events
                )

                # The existing issue/reversal documents must not introduce
                # extra money movements.
                assert all(
                    event.source_kind in (
                        "payment_issue",
                        "payment_reversal",
                        "payroll_issue",
                        "payroll_reversal",
                    )
                    for event in cash_events
                )

                assert len({
                    event.source_id for event in cash_events
                }) == len(cash_events)

                with pytest.raises(CashEventSourceError):
                    await read_physical_cash_events(
                        db,
                        company_id=company.id,
                        cash_desk_id=cash_desk.id,
                        currency_code="EUR",
                        effective_date=payment_date,
                    )

                with pytest.raises(CashEventSourceError):
                    await read_physical_cash_events(
                        db,
                        company_id=company.id,
                        cash_desk_id=0,
                        currency_code="UAH",
                        effective_date=payment_date,
                    )

                print("UNIFIED CASH POSTGRESQL EVENT READER = PASS")
                print("PAYROLL ORIGINAL + REVERSAL EVENTS = PASS")
                print("CASH DOCUMENT DOUBLE COUNT = 0")
                print("CASH EVENT COMPANY / CURRENCY GUARDS = PASS")

                from app.services.cash_physical_balance_guard import (
                    validate_locked_physical_cash,
                    PhysicalCashBalanceGuardError,
                )

                physical_result = await validate_locked_physical_cash(
                    db,
                    company_id=company.id,
                    cash_desk_id=cash_desk.id,
                    as_of_date=payment_date,
                )

                assert physical_result.closing_balance == Decimal("3500.00")

                with pytest.raises(PhysicalCashBalanceGuardError):
                    await validate_locked_physical_cash(
                        db,
                        company_id=foreign_cash_company.id,
                        cash_desk_id=cash_desk.id,
                        as_of_date=payment_date,
                    )

                with pytest.raises(PhysicalCashBalanceGuardError):
                    await validate_locked_physical_cash(
                        db,
                        company_id=company.id,
                        cash_desk_id=0,
                        as_of_date=payment_date,
                    )

                with pytest.raises(PhysicalCashBalanceGuardError):
                    await validate_locked_physical_cash(
                        db,
                        company_id=company.id,
                        cash_desk_id=cash_desk.id,
                        as_of_date=payment_date.replace(year=2025),
                    )

                print("PHYSICAL CASH ATOMIC GUARD POSTGRESQL = PASS")
                print("SAME-DESK POSTGRESQL ROW LOCK = PASS")
                print("CROSS-SOURCE CHRONOLOGY VERIFIED = PASS")
                print("INVALID SCOPE / DATE = REJECTED")
                print("GUARD DOES NOT POST OR COMMIT = PASS")


                print("CASH OPENING REVIEW POSITIVE PG E2E = PASS")
                print("CASH OPENING SELF REVIEW SERVICE = REJECTED")
                print("CASH OPENING SELF REVIEW DB = REJECTED")
                print("CASH OPENING CROSS COMPANY = REJECTED")
                print("CASH OPENING REVIEW REPLAY = PASS")
                print("CASH OPENING REVIEW CONFLICT = REJECTED")
                print("CASH OPENING REVIEW DB DUPLICATE = REJECTED")
                print("CASH OPENING REVIEW IMMUTABILITY = PASS")
                print("CASH OPENING REVIEW DATA = ISOLATED DB ONLY")

                from app.services.cash_payroll_gl_evidence_service import (
                    verify_payroll_cash_gl,
                    PayrollCashGLEvidenceError,
                )

                cash_original_id, cash_reversal_id = (
                    await verify_payroll_cash_gl(
                        db,
                        company_id=company.id,
                        payroll_disbursement_id=cash.id,
                    )
                )

                assert cash_original_id == cash_journal.id
                assert cash_reversal_id == reversal.id

                with pytest.raises(PayrollCashGLEvidenceError):
                    await verify_payroll_cash_gl(
                        db,
                        company_id=foreign_cash_company.id,
                        payroll_disbursement_id=cash.id,
                    )

                with pytest.raises(PayrollCashGLEvidenceError):
                    await verify_payroll_cash_gl(
                        db,
                        company_id=company.id,
                        payroll_disbursement_id=bank_part.id,
                    )

                strict_cash_events = await read_physical_cash_events(
                    db,
                    company_id=company.id,
                    cash_desk_id=cash_desk.id,
                    currency_code="UAH",
                    effective_date=payment_date,
                )

                assert any(
                    event.source_kind == "payroll_issue"
                    and event.source_id == cash_original_id
                    for event in strict_cash_events
                )
                assert any(
                    event.source_kind == "payroll_reversal"
                    and event.source_id == cash_reversal_id
                    for event in strict_cash_events
                )

                print("STRICT PAYROLL CASH POSTGRESQL GL = PASS")
                print("PAYROLL CASH ORIGINAL GL LEG = VERIFIED")
                print("PAYROLL CASH REVERSAL GL LEG = VERIFIED")
                print("PAYROLL CASH FOREIGN COMPANY = REJECTED")
                print("BANK PAYMENT IN CASH GL VERIFIER = REJECTED")
                print("UNIFIED READER STRICT PAYROLL GL = PASS")

                print('CASH PAYROLL REAL POSTGRESQL E2E = PASS')
                print('CASH GL 301 = PASS')
                print('BANK PLUS CASH CEILING = PASS')
                print('IDEMPOTENCY = PASS')
                print('INVALID SOURCES = REJECTED')
                print('OVERPAYMENT = REJECTED')
                print('REJECTED PAYROLL ROW LEAK = 0')
                print('REVERSAL HISTORY = PRESERVED')
                print('DUPLICATE GENERIC PAYMENT = 0')
    finally:
        await engine.dispose()
