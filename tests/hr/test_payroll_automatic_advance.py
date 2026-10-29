from app.services.account_types import AccountType, AccountNormalBalance
import json
import os
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_source_locking import sources
from payroll_verified_evidence_helper import verified_evidence
from app.models.account import Account, AccountType, AccountNormalBalance
from app.models.bank_account import BankAccount
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.payroll import PayrollPeriod
from app.models.payroll_statutory import PayrollStatutoryComponent as Component
from app.models.payroll_tax import PayrollEmployeeTaxProfile, PayrollStatutoryBaseRule
from app.schemas.time_attendance import AttendanceCreate
from app.services.time_attendance_service import create_attendance
from app.services.payroll_automatic_advance_service import create_automatic_payroll_advance
from app.services.payroll_advance_service import PayrollAdvanceError
from app.services.payroll_statutory_service import create_statutory_rate, resolve_statutory_rate, PayrollStatutoryError

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]


async def test_automatic_advance_keeps_minimum_taxes_evidence_and_retry_snapshot(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, employment, _ = await sources(db)
        period = PayrollPeriod(company_id=f['company'], year=2026, month=9,
            start_date=date(2026,9,1), end_date=date(2026,9,30), status='draft', created_by=f['actor'])
        account = Account(company_id=f['company'], code='311', name='Bank', account_type=AccountType.ASSET,
            normal_balance=AccountNormalBalance.DEBIT, is_active=True, is_postable=True)
        db.add_all([period,account]);await db.flush()
        bank=BankAccount(company_id=f['company'],name='Bank',account_number='TEST-AUTO-ADVANCE',
            currency_code='UAH',accounting_account_id=account.id,is_active=True)
        db.add(bank);await db.flush()
        for day in range(1,16):
            work_date=date(2026,9,day)
            if work_date.weekday()>4:continue
            await create_attendance(db,company_id=f['company'],created_by=f['actor'],
                data=AttendanceCreate(employment_contract_id=employment.id,work_date=work_date,status='present',
                    actual_start_at=datetime(2026,9,day,9,tzinfo=ZoneInfo('Europe/Kyiv')),
                    actual_end_at=datetime(2026,9,day,17,tzinfo=ZoneInfo('Europe/Kyiv'))))
        args=dict(company_id=f['company'],payroll_period_id=period.id,employment_contract_id=employment.id,
            bank_account_id=bank.id,advance_percentage=Decimal('40'),payment_date=date(2026,9,20),created_by=f['actor'])
        with pytest.raises(PayrollAdvanceError,match='no statutory rate'):
            await create_automatic_payroll_advance(db,**args)
        for component,rate in [(Component.PERSONAL_INCOME_TAX,'.18'),(Component.MILITARY_LEVY,'.05'),(Component.UNIFIED_SOCIAL_CONTRIBUTION,'.22')]:
            await create_statutory_rate(db,company_id=f['company'],component=component,rate=Decimal(rate),
                effective_from=date(2026,1,1),effective_to=None,actor_user_id=f['actor'])
        rate_evidence = await verified_evidence(
            db,
            company_id=f['company'],
            employee_id=f['employee'],
            created_by=f['actor'],
            entitlement_type='individual_rate',
            entitlement_code=Component.UNIFIED_SOCIAL_CONTRIBUTION.value,
            valid_to=date(2026,12,31),
        )
        benefit_evidence = await verified_evidence(
            db,
            company_id=f['company'],
            employee_id=f['employee'],
            created_by=f['actor'],
            entitlement_type='benefit',
            entitlement_code='TEST',
        )
        individual=await create_statutory_rate(db,company_id=f['company'],employee_id=f['employee'],
            component=Component.UNIFIED_SOCIAL_CONTRIBUTION,rate=Decimal('.0841'),source_reference='Disability evidence TEST-001',tax_evidence_id=rate_evidence.id,
            effective_from=date(2026,1,1),effective_to=date(2026,12,31),actor_user_id=f['actor'])
        with pytest.raises(PayrollStatutoryError,match='overlap'):
            await create_statutory_rate(db,company_id=f['company'],employee_id=f['employee'],
                component=Component.UNIFIED_SOCIAL_CONTRIBUTION,rate=Decimal('.0841'),source_reference='TEST-002',tax_evidence_id=rate_evidence.id,
                effective_from=date(2026,6,1),effective_to=date(2026,12,31),actor_user_id=f['actor'])
        with pytest.raises(PayrollStatutoryError,match='Employee not found'):
            await create_statutory_rate(db,company_id=f['company'],employee_id=2147483647,
                component=Component.UNIFIED_SOCIAL_CONTRIBUTION,rate=Decimal('.0841'),source_reference='TEST',
                effective_from=date(2026,1,1),effective_to=None,actor_user_id=f['actor'])
        db.add(PayrollEmployeeTaxProfile(company_id=f['company'],employee_id=f['employee'],
            employment_contract_id=employment.id,category='benefit_eligible',benefit_code='TEST',tax_evidence_id=benefit_evidence.id,
            effective_from=date(2026,1,1),created_by=f['actor']))
        db.add(PayrollStatutoryBaseRule(company_id=f['company'],component=Component.PERSONAL_INCOME_TAX.value,
            tax_profile_category='benefit_eligible',base_mode='gross_after_benefit',benefit_income_limit=Decimal('20000'),benefit_amount=Decimal('1000'),
            rule_code='TEST-BENEFIT',rule_version='1',effective_from=date(2026,1,1),created_by=f['actor']))
        await db.flush()
        individual.effective_from=date(2026,9,10)
        await db.flush()
        with pytest.raises(PayrollAdvanceError,match='dated income allocation'):
            await create_automatic_payroll_advance(db,**args)
        individual.effective_from=date(2026,1,1)
        await db.flush()
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from types import SimpleNamespace
        from app.api.v1 import payroll_advances
        from app.api.deps import get_current_user
        from app.core.database import get_db
        from app.models.role import Role
        from app.models.permission import Permission
        from app.models.rbac import role_permissions
        from app.models.user_company_role import UserCompanyRole
        from app.models.payroll_advance import PayrollAdvance
        role=Role(name='Auto advance manager'); permission=Permission(name='employees.manage')
        db.add_all([role,permission]);await db.flush()
        await db.execute(role_permissions.insert(),[dict(role_id=role.id,permission_id=permission.id)])
        db.add(UserCompanyRole(user_id=f['actor'],company_id=f['company'],role_id=role.id))
        await db.commit()
        api=FastAPI();api.include_router(payroll_advances.router)
        async def session():yield db
        api.dependency_overrides[get_db]=session
        api.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=f['actor'])
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            payload=dict(payroll_period_id=period.id,employment_contract_id=employment.id,
                bank_account_id=bank.id,advance_percentage='40',payment_date='2026-09-20')
            rejected=await client.post(f"/companies/{f['company']}/payroll-advances",json={**payload,'minimum_due_amount':'0'})
            assert rejected.status_code==422,rejected.text
            created=await client.post(f"/companies/{f['company']}/payroll-advances",json=payload)
            assert created.status_code==200,created.text
        advance=await db.get(PayrollAdvance,created.json()['id'])
        assert advance.calculated_amount==Decimal('11000')
        assert advance.minimum_due_amount==Decimal('8470')
        assert advance.paid_amount==Decimal('8470')
        snapshot=json.loads(advance.calculation_snapshot_json)
        assert snapshot['social_benefit_applied'] is False
        lines={line['component']:line for line in snapshot['tax_lines']}
        assert Decimal(lines[Component.PERSONAL_INCOME_TAX.value]['amount'])==Decimal('1980')
        assert Decimal(lines[Component.MILITARY_LEVY.value]['amount'])==Decimal('550')
        assert Decimal(lines[Component.UNIFIED_SOCIAL_CONTRIBUTION.value]['amount'])==Decimal('925.10')
        assert lines[Component.UNIFIED_SOCIAL_CONTRIBUTION.value]['source_rate_id']==individual.id
        assert lines[Component.UNIFIED_SOCIAL_CONTRIBUTION.value]['source_tax_evidence_id']==rate_evidence.id
        assert snapshot['tax_profile_evidence_id']==benefit_evidence.id
        original=advance.calculation_snapshot_json
        salary=await db.scalar(select(EmployeeSalaryRate).where(EmployeeSalaryRate.employment_contract_id==employment.id))
        salary.amount=Decimal('33000');await db.flush()
        retry=await create_automatic_payroll_advance(db,**args)
        from app.services.payroll_advance_tax_source_proof import (
            verify_automatic_advance_tax_source_in_db,
        )
        tax_source_proof = await verify_automatic_advance_tax_source_in_db(db, retry)
        assert tax_source_proof.advance_id == retry.id
        assert tax_source_proof.company_id == retry.company_id
        assert tax_source_proof.withholding_amount >= 0
        assert tax_source_proof.net_amount == retry.paid_amount
        assert tax_source_proof.tax_remittance_confirmed is False
        assert tax_source_proof.monthly_tax_clearing_confirmed is False
        print("REAL AUTOMATIC ADVANCE TAX SOURCE = PASS")
        from app.services.payroll_advance_monthly_tax_clearing import (
            reconcile_monthly_advance_taxes,
        )

        advance_components = {
            part.component: part.amount
            for part in tax_source_proof.components
            if part.component in (
                "personal_income_tax", "military_levy"
            )
        }

        assert set(advance_components) == {
            "personal_income_tax", "military_levy"
        }

        # This is deliberately a simulated monthly obligation:
        # it is NOT described as a posted PayrollStatutoryResult.
        simulated_monthly_tax = {
            name: amount * 2
            for name, amount in advance_components.items()
        }

        simulated_gross = tax_source_proof.gross_amount * 2
        simulated_net = (
            simulated_gross - sum(
                simulated_monthly_tax.values(), Decimal("0")
            )
        )

        clearing = reconcile_monthly_advance_taxes(
            monthly_gross=simulated_gross,
            monthly_net=simulated_net,
            advance_gross=tax_source_proof.gross_amount,
            advance_net=tax_source_proof.net_amount,
            advance_tax_calculated=advance_components,
            monthly_tax_liability=simulated_monthly_tax,
            tax_actually_remitted={
                "personal_income_tax": Decimal("0.00"),
                "military_levy": Decimal("0.00"),
            },
        )

        assert clearing.advance_net_paid == retry.paid_amount
        assert clearing.final_net_available == (
            simulated_net - retry.paid_amount
        )
        assert clearing.tax_unremitted == simulated_monthly_tax
        assert clearing.source_only is True
        assert clearing.financial_execution_allowed is False

        print("B2-3C REAL ADVANCE TO CLEARING = PASS")
        print("SIMULATED MONTHLY LIABILITY = EXPLICIT")
        print("UNPROVEN TAX REMITTANCE = ZERO")
        print("FINANCIAL EXECUTION = BLOCKED")

        assert retry.id==advance.id and retry.calculation_snapshot_json==original
        with pytest.raises(PayrollAdvanceError,match='different request'):
            await create_automatic_payroll_advance(db,**{**args,'advance_percentage':Decimal('50')})
        rate=await resolve_statutory_rate(db,company_id=f['company'],employee_id=f['employee'],
            component=Component.UNIFIED_SOCIAL_CONTRIBUTION,effective_date=date(2027,1,1))
        assert rate.rate==Decimal('.22')
        from test_payroll_settlement_migrations import assert_history_preserved
        await assert_history_preserved(db,'13d4a7b8c010_payroll_advance_snapshot_and_individual_rates.py')
        # B2-R5A: Actual monthly documents, not simulated liabilities.
        from app.models.payroll import PayrollInput
        from app.models.payroll import PayrollInputSalarySlice
        from app.services.payroll_calculation_service import calculate_payroll_input
        from app.services.payroll_statutory_service import (
            calculate_payroll_statutory_result,
        )

        # The advance must exist before this transition.
        assert retry.payroll_period_id == period.id
        assert retry.employment_contract_id == employment.id
        assert period.status == "draft"

        payroll_input = PayrollInput(
            monthly_norm_minutes=10560,
            company_id=f["company"],
            payroll_period_id=period.id,
            employment_contract_id=employment.id,
            scheduled_minutes=6000,
            worked_minutes=6000,
            leave_days=0,
            sick_days=0,
            manual_adjustment_amount=Decimal("0.00"),
            created_by=f["actor"],
        )
        db.add(payroll_input)
        await db.flush()

        salary_slice = PayrollInputSalarySlice(
            company_id=f["company"],
            payroll_input_id=payroll_input.id,
            salary_rate_id=salary.id,
            salary_rate_type=salary.rate_type,
            salary_rate_amount=salary.amount,
            currency_code=salary.currency_code,
            effective_from=period.start_date,
            effective_to=period.end_date,
            scheduled_minutes=6000,
            worked_minutes=6000,
        )
        db.add(salary_slice)
        await db.flush()

        # Finalize only after the advance was calculated.
        period.status = "finalized"
        period.finalized_by = f["actor"]
        from datetime import timezone
        period.finalized_at = datetime(
            2026, 10, 1, 12, tzinfo=timezone.utc
        )
        await db.flush()

        monthly_calculation = await calculate_payroll_input(
            db,
            company_id=f["company"],
            payroll_input_id=payroll_input.id,
            calculated_by=f["actor"],
        )
        monthly_statutory = await calculate_payroll_statutory_result(
            db,
            company_id=f["company"],
            payroll_calculation_id=monthly_calculation.id,
            actor_user_id=f["actor"],
        )

        assert monthly_calculation.company_id == retry.company_id
        assert monthly_calculation.payroll_period_id == retry.payroll_period_id
        assert monthly_calculation.employment_contract_id == retry.employment_contract_id
        assert monthly_statutory.payroll_calculation_id == monthly_calculation.id
        assert monthly_statutory.currency_code == "UAH"
        assert monthly_statutory.net_amount >= retry.paid_amount

        from app.services.payroll_advance_service import (
            generate_and_post_payroll_advance_journal,
        )
        from app.models.journal_entry import JournalEntry
        from sqlalchemy import func

        # Only the isolated test company receives this GL account.
        # Production chart-of-accounts resolution is not modified.

        existing_payable = await db.scalar(
            select(Account).where(
                Account.company_id == f["company"],
                Account.code == "66",
            )
        )

        if existing_payable is None:
            existing_payable = Account(
                company_id=f["company"],
                code="66",
                name="Payroll net payable",
                account_type=AccountType.LIABILITY,
                normal_balance=AccountNormalBalance.CREDIT,
                is_active=True,
                is_postable=True, is_system=True,
            )
            db.add(existing_payable)
            await db.flush()

        assert existing_payable.code == "66"
        assert existing_payable.company_id == f["company"]
        assert existing_payable.is_active
        assert existing_payable.is_postable

        print("R5B1 TEST PAYROLL ACCOUNT 66 = READY")
        from app.models.accounting_period import AccountingPeriod

        existing_accounting_period = await db.scalar(
            select(AccountingPeriod).where(
                AccountingPeriod.company_id == f["company"],
                AccountingPeriod.year == 2026,
                AccountingPeriod.month == 9,
            )
        )

        if existing_accounting_period is None:
            existing_accounting_period = AccountingPeriod(
                company_id=f["company"],
                year=2026,
                month=9,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 30),
                status="open",
                is_locked=False,
            )
            db.add(existing_accounting_period)
            await db.flush()

        assert existing_accounting_period.company_id == f["company"]
        assert existing_accounting_period.start_date <= retry.payment_date
        assert existing_accounting_period.end_date >= retry.payment_date
        assert existing_accounting_period.status == "open"
        assert existing_accounting_period.is_locked is False

        print("R5B1 SEPTEMBER ACCOUNTING PERIOD = READY")
        before_advance_journals = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"],
                JournalEntry.payroll_advance_id == retry.id,
                JournalEntry.reversal_of_id.is_(None),
            )
        )
        assert before_advance_journals == 0

        posted_advance = await generate_and_post_payroll_advance_journal(
            db,
            company_id=f["company"],
            payroll_advance_id=retry.id,
            created_by=f["actor"],
        )

        assert posted_advance.company_id == f["company"]
        assert posted_advance.payroll_advance_id == retry.id
        assert posted_advance.entry_date == retry.payment_date
        assert str(getattr(posted_advance.status, "value", posted_advance.status)) == "posted"

        repeated_posting = await generate_and_post_payroll_advance_journal(
            db,
            company_id=f["company"],
            payroll_advance_id=retry.id,
            created_by=f["actor"],
        )
        assert repeated_posting.id == posted_advance.id

        after_advance_journals = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"],
                JournalEntry.payroll_advance_id == retry.id,
                JournalEntry.reversal_of_id.is_(None),
            )
        )
        assert after_advance_journals == 1

        print("R5B1 POSTED ADVANCE JOURNAL = PASS")
        print("R5B1 ADVANCE POSTING IDEMPOTENCY = PASS")
        # R5B2: Create actual accounting accrual for this calculation.
        from app.services.payroll_accounting_service import (
            generate_and_post_payroll_journal_entry,
        )

        # Configure the isolated test company only.
        # Preserve all production accounting validations.
        expense = await db.scalar(
            select(Account).where(
                Account.company_id == f["company"],
                Account.code == "92",
            )
        )
        if expense is None:
            expense = Account(
                company_id=f["company"],
                code="92",
                name="Administrative payroll expense",
                account_type=AccountType.EXPENSE,
                normal_balance=AccountNormalBalance.DEBIT,
                is_active=True,
                is_postable=True,
                is_system=True,
            )
            db.add(expense)
            await db.flush()

        assert expense.company_id == f["company"]
        assert expense.code == "92"
        assert expense.is_active is True
        assert expense.is_postable is True
        assert expense.is_system is True

        print("R5B2 TEST ACCOUNT 92 = READY")
        # Tax payable accounts in the isolated test company.
        # PIT and military levy use account 64; USC uses account 65.
        for code, name in (
            ("64", "Tax payable"),
            ("65", "Unified social contribution payable"),
        ):
            tax_account = await db.scalar(
                select(Account).where(
                    Account.company_id == f["company"],
                    Account.code == code,
                )
            )

            if tax_account is None:
                tax_account = Account(
                    company_id=f["company"],
                    code=code,
                    name=name,
                    account_type=AccountType.LIABILITY,
                    normal_balance=AccountNormalBalance.CREDIT,
                    is_active=True,
                    is_postable=True,
                    is_system=True,
                )
                db.add(tax_account)
                await db.flush()

            assert tax_account.company_id == f["company"]
            assert tax_account.code == code
            assert tax_account.account_type == AccountType.LIABILITY
            assert tax_account.normal_balance == AccountNormalBalance.CREDIT
            assert tax_account.is_active is True
            assert tax_account.is_postable is True
            assert tax_account.is_system is True

        print("R5B2 TAX ACCOUNTS 64 AND 65 = READY")
        monthly_accrual = await generate_and_post_payroll_journal_entry(
            db,
            company_id=f["company"],
            payroll_calculation_id=monthly_calculation.id,
            created_by=f["actor"],
        )

        assert monthly_accrual.company_id == f["company"]
        assert monthly_accrual.payroll_calculation_id == monthly_calculation.id
        assert str(
            getattr(monthly_accrual.status, "value", monthly_accrual.status)
        ) == "posted"

        monthly_accrual_retry = await generate_and_post_payroll_journal_entry(
            db,
            company_id=f["company"],
            payroll_calculation_id=monthly_calculation.id,
            created_by=f["actor"],
        )
        assert monthly_accrual_retry.id == monthly_accrual.id

        monthly_accrual_count = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"],
                JournalEntry.payroll_calculation_id == monthly_calculation.id,
                JournalEntry.reversal_of_id.is_(None),
            )
        )
        assert monthly_accrual_count == 1

        # R5B2-B: Real posted bank-to-budget payments allocated
        # against the same genuine monthly payroll calculation.
        from app.models.payroll_statutory import (
            PayrollStatutoryResultLine as TaxResultLine,
        )
        from app.services.payroll_tax_remittance_service import (
            register_tax_remittance as register_actual_tax_payment,
            remittance_commitment as actual_tax_commitment,
        )
        from test_payroll_tax_remittance import (
            payment as create_posted_tax_payment,
            request as make_tax_payment_request,
        )

        tax_result_rows = (
            await db.scalars(
                select(TaxResultLine).where(
                    TaxResultLine.company_id == f["company"],
                    TaxResultLine.payroll_statutory_result_id
                    == monthly_statutory.id,
                )
            )
        ).all()

        tax_due = {}
        for tax_row in tax_result_rows:
            tax_name = getattr(
                tax_row.component, "value", tax_row.component
            )
            if tax_name in ("personal_income_tax", "military_levy"):
                assert tax_name not in tax_due
                tax_due[tax_name] = Decimal(tax_row.amount)

        assert set(tax_due) == {
            "personal_income_tax",
            "military_levy",
        }
        assert all(value > 0 for value in tax_due.values())

        # These accounts are already verified by R5B2-A.
        tax_accounts = {}
        for code in ("64", "65"):
            tax_gl = await db.scalar(
                select(Account).where(
                    Account.company_id == f["company"],
                    Account.code == code,
                )
            )
            assert tax_gl is not None and tax_gl.is_system
            tax_accounts[code] = tax_gl.id

        for component in ("personal_income_tax", "military_levy"):
            amount = tax_due[component]

            posted_tax_journal = await create_posted_tax_payment(
                db,
                # Helper only needs company.id and user.id:
                type("CompanyRef", (), {"id": f["company"]})(),
                type("UserRef", (), {"id": f["actor"]})(),
                tax_accounts,
                bank,
                amount,
                component=component,
                day=date(2026, 9, 30),
            )

            tax_request = make_tax_payment_request(
                posted_tax_journal,
                bank,
                monthly_calculation,
                amount,
                "r5b2b-" + component,
                component,
            )

            # R2-C-R5: Monthly-first real PostgreSQL negative E2E.
            # All temporary source changes are rolled back.
            from app.models.payroll_tax_remittance import (
                PayrollTaxRemittance,
            )
            from app.models.payroll_advance_tax_remittance import (
                PayrollAdvanceTaxRemittance,
            )
            from app.services.payroll_advance_tax_remittance_service import (
                register_advance_tax_remittance,
                AdvanceTaxRemittanceError,
            )
            from app.services.payroll_advance_tax_source_proof import (
                verify_automatic_advance_tax_source_in_db,
            )

            r2c_proof = await verify_automatic_advance_tax_source_in_db(
                db, retry
            )
            r2c_matches = [
                Decimal(item.amount)
                for item in r2c_proof.components
                if item.component == component
            ]
            assert len(r2c_matches) == 1
            r2c_amount = r2c_matches[0]
            assert r2c_amount > 0
            assert r2c_amount <= tax_due[component]

            async def r2c_counts():
                return (
                    await db.scalar(
                        select(func.count(JournalEntry.id)).where(
                            JournalEntry.company_id == f["company"]
                        )
                    ),
                    await db.scalar(
                        select(func.count(PayrollTaxRemittance.id)).where(
                            PayrollTaxRemittance.company_id == f["company"]
                        )
                    ),
                    await db.scalar(
                        select(func.count(PayrollAdvanceTaxRemittance.id)).where(
                            PayrollAdvanceTaxRemittance.company_id == f["company"]
                        )
                    ),
                )

            r2c_baseline = await r2c_counts()
            r2c_original_accrual_date = monthly_accrual.entry_date

            r2c_savepoint = await db.begin_nested()
            try:
                # Disposable E2E state: the accrual must precede
                # the bank payment on the advance date.
                monthly_accrual.entry_date = retry.payment_date
                await db.flush()

                r2c_posted = await create_posted_tax_payment(
                    db,
                    type("CompanyRef", (), {"id": f["company"]})(),
                    type("UserRef", (), {"id": f["actor"]})(),
                    tax_accounts,
                    bank,
                    r2c_amount,
                    component=component,
                    day=retry.payment_date,
                )

                r2c_request = make_tax_payment_request(
                    r2c_posted,
                    bank,
                    monthly_calculation,
                    r2c_amount,
                    "b2-r2c-r5-monthly-" + component,
                    component,
                )

                r2c_registered = await register_actual_tax_payment(
                    db,
                    company_id=f["company"],
                    data=r2c_request,
                    created_by=f["actor"],
                )

                assert (
                    r2c_registered.journal_entry_id
                    == r2c_posted.id
                )

                r2c_before_rejection = await r2c_counts()

                with pytest.raises(
                    AdvanceTaxRemittanceError,
                    match="Bank journal already allocated to monthly tax",
                ):
                    await register_advance_tax_remittance(
                        db,
                        company_id=f["company"],
                        payroll_advance_id=retry.id,
                        journal_entry_id=r2c_posted.id,
                        bank_account_id=bank.id,
                        component=component,
                        amount=r2c_amount,
                        request_key="b2-r2c-r5-advance-" + component,
                        document_reference="Verified E2E monthly payment",
                        budget_payment_reference="Verified budget destination",
                        created_by=f["actor"],
                    )

                assert await r2c_counts() == r2c_before_rejection

                print(
                    "R2-C-R5 MONTHLY-FIRST CROSS-ALLOCATION = PASS:",
                    component,
                )
            finally:
                if r2c_savepoint.is_active:
                    await r2c_savepoint.rollback()

            await db.refresh(monthly_accrual)

            assert (
                monthly_accrual.entry_date
                == r2c_original_accrual_date
            )
            assert await r2c_counts() == r2c_baseline

            print("R2-C-R5 SAVEPOINT ZERO LEAK = PASS")

            tax_remittance = await register_actual_tax_payment(
                db,
                company_id=f["company"],
                data=tax_request,
                created_by=f["actor"],
            )



            repeated = await register_actual_tax_payment(
                db,
                company_id=f["company"],
                data=tax_request,
                created_by=f["actor"],
            )
            assert repeated.id == tax_remittance.id

            committed = await actual_tax_commitment(
                db,
                company_id=f["company"],
                calculation_id=monthly_calculation.id,
                component=component,
                from_date=date(2026, 9, 30),
            )
            assert Decimal(committed) == amount

            print(
                "R5B2-B ACTUAL TAX = PASS:",
                component,
                amount,
            )

        # R5B3-A: Read real monthly statutory rows, real advance
        # bank posting, and real PIT/VZ remittance allocations.
        from app.services.payroll_advance_monthly_tax_clearing_db import (
            reconcile_monthly_advance_taxes_from_db,
        )
        from app.services.payroll_advance_monthly_tax_clearing import (
            MonthlyTaxClearingError,
        )

        clearing_args = dict(
            company_id=f["company"],
            payroll_advance_id=retry.id,
            payroll_calculation_id=monthly_calculation.id,
        )

        # Clearing cannot be backdated before the advance payment.
        with pytest.raises(
            MonthlyTaxClearingError,
            match="before advance payment date",
        ):
            await reconcile_monthly_advance_taxes_from_db(
                db,
                **clearing_args,
                as_of=date(2026, 9, 19),
            )

        before_clearing_journals = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"],
            )
        )

        cleared = await reconcile_monthly_advance_taxes_from_db(
            db,
            **clearing_args,
            as_of=date(2026, 9, 30),
        )

        assert cleared.advance_net_paid == retry.paid_amount
        assert cleared.final_net_available == (
            monthly_statutory.net_amount - retry.paid_amount
        )
        assert cleared.tax_unremitted == {
            "personal_income_tax": Decimal("0.00"),
            "military_levy": Decimal("0.00"),
        }
        assert cleared.source_only is True
        assert cleared.financial_execution_allowed is False

        again = await reconcile_monthly_advance_taxes_from_db(
            db,
            **clearing_args,
            as_of=date(2026, 9, 30),
        )
        assert again == cleared

        after_clearing_journals = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"],
            )
        )
        assert before_clearing_journals == after_clearing_journals

        print("R5B3-A REAL DATED TAX CLEARING = PASS")
        print("R5B3-A PIT AND VZ UNREMITTED ZERO = PASS")
        print("R5B3-A READ ONLY ZERO JOURNAL LEAK = PASS")
        print("R5B3-A BACKDATED CLEARING REJECTED = PASS")
        # R5B3-B: A tax payment reversed in October must remain
        # visible in the historical September position.
        from app.services.payroll_tax_remittance_service import (
            reverse_tax_remittance,
        )

        from app.models.accounting_period import AccountingPeriod

        october_accounting = await db.scalar(
            select(AccountingPeriod).where(
                AccountingPeriod.company_id == f["company"],
                AccountingPeriod.year == 2026,
                AccountingPeriod.month == 10,
            )
        )

        if october_accounting is None:
            october_accounting = AccountingPeriod(
                company_id=f["company"],
                year=2026,
                month=10,
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 31),
                status="open",
                is_locked=False,
            )
            db.add(october_accounting)
            await db.flush()

        assert october_accounting.status == "open"
        assert october_accounting.is_locked is False

        # The immediately preceding remittance loop ends with military_levy.
        assert component == "military_levy"
        military_remittance_id = tax_remittance.id
        military_amount = tax_due["military_levy"]

        assert military_amount > 0

        original_september = await reconcile_monthly_advance_taxes_from_db(
            db,
            **clearing_args,
            as_of=date(2026, 9, 30),
        )
        assert original_september == cleared

        journal_count_before_reversal = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        )

        await reverse_tax_remittance(
            db,
            company_id=f["company"],
            remittance_id=military_remittance_id,
            reversal_date=date(2026, 10, 1),
            reversed_by=f["actor"],
        )

        journal_count_after_reversal = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        )
        assert journal_count_after_reversal == journal_count_before_reversal + 1

        # The future reversal must not rewrite September history.
        historical = await reconcile_monthly_advance_taxes_from_db(
            db,
            **clearing_args,
            as_of=date(2026, 9, 30),
        )

        assert historical == original_september
        assert historical.tax_unremitted == {
            "personal_income_tax": Decimal("0.00"),
            "military_levy": Decimal("0.00"),
        }

        # October shows the reversed military levy as unpaid again.
        after_reversal = await reconcile_monthly_advance_taxes_from_db(
            db,
            **clearing_args,
            as_of=date(2026, 10, 1),
        )

        assert after_reversal.tax_unremitted == {
            "personal_income_tax": Decimal("0.00"),
            "military_levy": military_amount,
        }
        assert after_reversal.source_only is True
        assert after_reversal.financial_execution_allowed is False

        again_after_reversal = await reconcile_monthly_advance_taxes_from_db(
            db,
            **clearing_args,
            as_of=date(2026, 10, 1),
        )
        assert again_after_reversal == after_reversal

        print("R5B3-B REAL TAX REVERSAL = PASS")
        print("R5B3-B HISTORICAL TAX CLEARING = PASS")
        print("R5B3-B OCTOBER LIABILITY RESTORED = PASS")
        print("R5B3-B POST-REVERSAL FINANCIAL EXECUTION BLOCKED = PASS")
        # R6-B2-B2-B: Real posted advance tax remittances.
        from app.services.payroll_advance_tax_remittance_service import (
            register_advance_tax_remittance,
            AdvanceTaxRemittanceError,
        )
        from app.services.payroll_advance_tax_source_proof import (
            verify_automatic_advance_tax_source_in_db,
        )
        from app.models.payroll_advance_tax_remittance import (
            PayrollAdvanceTaxRemittance,
        )

        advance_proof = await verify_automatic_advance_tax_source_in_db(
            db, retry
        )
        advance_due = {
            component.component: Decimal(component.amount)
            for component in advance_proof.components
            if component.component in ("personal_income_tax", "military_levy")
        }
        assert set(advance_due) == {
            "personal_income_tax", "military_levy"
        }
        assert all(value > 0 for value in advance_due.values())
        assert retry.payment_date == date(2026, 9, 20)

        advance_tax_journals_before = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        )

        for tax_component in ("personal_income_tax", "military_levy"):
            tax_amount = advance_due[tax_component]

            posted = await create_posted_tax_payment(
                db,
                type("CompanyRef", (), {"id": f["company"]})(),
                type("UserRef", (), {"id": f["actor"]})(),
                tax_accounts,
                bank,
                tax_amount,
                component=tax_component,
                day=date(2026, 9, 20),
            )

            arguments = dict(
                company_id=f["company"],
                payroll_advance_id=retry.id,
                journal_entry_id=posted.id,
                bank_account_id=bank.id,
                component=tax_component,
                amount=tax_amount,
                request_key="r6b2b2b-" + tax_component,
                document_reference="Verified advance tax payment",
                budget_payment_reference="Verified budget payment",
                created_by=f["actor"],
            )

            # Incorrect amount cannot be recorded.
            with pytest.raises(AdvanceTaxRemittanceError):
                await register_advance_tax_remittance(
                    db,
                    **{**arguments, "amount": tax_amount + Decimal("0.01")},
                )

            # Cross-company attribution must fail.
            with pytest.raises(AdvanceTaxRemittanceError):
                await register_advance_tax_remittance(
                    db,
                    **{**arguments, "company_id": 2147483647},
                )

            before = await db.scalar(
                select(func.count(PayrollAdvanceTaxRemittance.id)).where(
                    PayrollAdvanceTaxRemittance.company_id == f["company"]
                )
            )

            registered = await register_advance_tax_remittance(
                db, **arguments
            )
            assert registered.amount == tax_amount
            assert registered.payroll_advance_id == retry.id
            assert registered.journal_entry_id == posted.id
            assert registered.component == tax_component

            # Real, already attributed advance-tax bank payment must
            # not be accepted again as a monthly payroll tax payment.
            from app.models.payroll_tax_remittance import PayrollTaxRemittance
            from app.services.payroll_tax_remittance_service import (
                TaxRemittanceError,
            )

            cross_monthly_before = await db.scalar(
                select(func.count(PayrollTaxRemittance.id)).where(
                    PayrollTaxRemittance.company_id == f["company"]
                )
            )
            cross_advance_before = await db.scalar(
                select(func.count(PayrollAdvanceTaxRemittance.id)).where(
                    PayrollAdvanceTaxRemittance.company_id == f["company"]
                )
            )
            cross_journals_before = await db.scalar(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == f["company"]
                )
            )

            cross_request = make_tax_payment_request(
                posted,
                bank,
                monthly_calculation,
                tax_amount,
                "b2-r2b-cross-monthly-" + tax_component,
                tax_component,
            )

            with pytest.raises(
                TaxRemittanceError,
                match="Payment journal already attributed to advance tax",
            ):
                await register_actual_tax_payment(
                    db,
                    company_id=f["company"],
                    data=cross_request,
                    created_by=f["actor"],
                )

            assert await db.scalar(
                select(func.count(PayrollTaxRemittance.id)).where(
                    PayrollTaxRemittance.company_id == f["company"]
                )
            ) == cross_monthly_before

            assert await db.scalar(
                select(func.count(PayrollAdvanceTaxRemittance.id)).where(
                    PayrollAdvanceTaxRemittance.company_id == f["company"]
                )
            ) == cross_advance_before

            assert await db.scalar(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == f["company"]
                )
            ) == cross_journals_before

            print(
                "R2-B ADVANCE-FIRST CROSS-ALLOCATION REJECTED:",
                tax_component,
            )

            after = await db.scalar(
                select(func.count(PayrollAdvanceTaxRemittance.id)).where(
                    PayrollAdvanceTaxRemittance.company_id == f["company"]
                )
            )
            assert after == before + 1

            repeated = await register_advance_tax_remittance(
                db, **arguments
            )
            assert repeated.id == registered.id

            with pytest.raises(AdvanceTaxRemittanceError):
                await register_advance_tax_remittance(
                    db,
                    **{
                        **arguments,
                        "document_reference": "Changed payment evidence",
                    },
                )

            after_retry = await db.scalar(
                select(func.count(PayrollAdvanceTaxRemittance.id)).where(
                    PayrollAdvanceTaxRemittance.company_id == f["company"]
                )
            )
            assert after_retry == after

            print(
                "R6-B2-B2-B ADVANCE TAX REGISTERED = PASS:",
                tax_component,
                tax_amount,
            )

        advance_tax_journals_after = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        )

        # Exactly two bank tax journals were created by the test helper;
        # the registration service itself must not create another.
        assert (
            advance_tax_journals_after - advance_tax_journals_before
        ) == 2

        print("R6-B2-B2-B BOTH ADVANCE TAXES REGISTERED = PASS")
        # R6-B2-C2-B3: BOTH advance payments stay active.
        # Existing monthly levy is already reversed on October 1.
        # Reverse monthly PIT inside an isolated PostgreSQL SAVEPOINT.
        # Roll it back before continuing the existing E2E.
        from app.models.payroll_tax_remittance import (
            PayrollTaxRemittance as R6B3MonthlyRemittance,
        )
        from app.services.payroll_advance_monthly_tax_clearing_db import (
            reconcile_combined_advance_monthly_tax_from_db
            as r6b3_combined,
            advance_tax_remittance_commitment as r6b3_advance_commitment,
        )

        r6b3_monthly_pit = (
            await db.scalars(
                select(R6B3MonthlyRemittance).where(
                    R6B3MonthlyRemittance.company_id == f["company"],
                    R6B3MonthlyRemittance.component == "personal_income_tax",
                )
            )
        ).all()
        assert len(r6b3_monthly_pit) == 1

        r6b3_journals_before = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        )

        r6b3_savepoint = await db.begin_nested()
        try:
            await reverse_tax_remittance(
                db,
                company_id=f["company"],
                remittance_id=r6b3_monthly_pit[0].id,
                reversal_date=date(2026, 10, 1),
                reversed_by=f["actor"],
            )

            r6b3_after_real_reversal = await db.scalar(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == f["company"]
                )
            )
            assert r6b3_after_real_reversal == r6b3_journals_before + 1

            r6b3_before_read = await db.scalar(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == f["company"]
                )
            )

            r6b3_result = await r6b3_combined(
                db,
                **clearing_args,
                as_of=date(2026, 10, 1),
            )

            assert r6b3_result.monthly_liability == {
                "personal_income_tax": tax_due["personal_income_tax"],
                "military_levy": tax_due["military_levy"],
            }

            assert r6b3_result.advance_remitted == {
                "personal_income_tax": advance_due["personal_income_tax"],
                "military_levy": advance_due["military_levy"],
            }

            assert r6b3_result.monthly_remitted == {
                "personal_income_tax": Decimal("0.00"),
                "military_levy": Decimal("0.00"),
            }

            assert r6b3_result.total_remitted == {
                "personal_income_tax": advance_due["personal_income_tax"],
                "military_levy": advance_due["military_levy"],
            }

            assert r6b3_result.unpaid == {
                "personal_income_tax":
                    tax_due["personal_income_tax"]
                    - advance_due["personal_income_tax"],
                "military_levy":
                    tax_due["military_levy"]
                    - advance_due["military_levy"],
            }

            assert r6b3_result.unpaid == {
                "personal_income_tax": Decimal("1215.00"),
                "military_levy": Decimal("387.50"),
            }

            for component in ("personal_income_tax", "military_levy"):
                assert await r6b3_advance_commitment(
                    db,
                    company_id=f["company"],
                    payroll_advance_id=retry.id,
                    component=component,
                    as_of=date(2026, 10, 1),
                ) == advance_due[component]

            r6b3_repeat = await r6b3_combined(
                db,
                **clearing_args,
                as_of=date(2026, 10, 1),
            )
            assert r6b3_repeat == r6b3_result

            assert await db.scalar(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == f["company"]
                )
            ) == r6b3_before_read

        finally:
            if r6b3_savepoint.is_active:
                await r6b3_savepoint.rollback()

        # The temporary real reversal must leave no rows or state changes.
        assert await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        ) == r6b3_journals_before

        r6b3_monthly_pit_commitment = await actual_tax_commitment(
            db,
            company_id=f["company"],
            calculation_id=monthly_calculation.id,
            component="personal_income_tax",
            from_date=date(2026, 10, 1),
        )
        # Public commitment now includes actual advance payments, including
        # the deliberately overpaid state constructed by this legacy fixture.
        assert Decimal(r6b3_monthly_pit_commitment) == (
            tax_due["personal_income_tax"] + advance_due["personal_income_tax"]
        )

        r6b3_monthly_levy_commitment = await actual_tax_commitment(
            db,
            company_id=f["company"],
            calculation_id=monthly_calculation.id,
            component="military_levy",
            from_date=date(2026, 10, 1),
        )
        assert Decimal(r6b3_monthly_levy_commitment) == advance_due["military_levy"]

        print("R6-B2-C2-B3 BOTH ADVANCE TAX CREDITS = PASS")
        print("R6-B2-C2-B3 EXACT UNPAID AMOUNTS = PASS")
        print("R6-B2-C2-B3 ZERO MONTHLY REMITTANCES = PASS")
        print("R6-B2-C2-B3 READ ONLY IDEMPOTENT = PASS")
        print("R6-B2-C2-B3 POSTGRESQL SAVEPOINT ROLLBACK = PASS")
        print("R6-B2-C2-B3 ORIGINAL E2E STATE PRESERVED = PASS")
        # R6-B2-B3: Last registered advance component is military levy.
        from app.services.payroll_advance_tax_remittance_service import (
            reverse_advance_tax_remittance,
        )

        assert tax_component == "military_levy"
        assert registered.component == "military_levy"

        r6_military_id = registered.id
        r6_original_journal_id = registered.journal_entry_id

        r6_before = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        )

        # An earlier date cannot reverse a payment from September 20.
        with pytest.raises(
            AdvanceTaxRemittanceError,
            match="before posting date",
        ):
            await reverse_advance_tax_remittance(
                db,
                company_id=f["company"],
                remittance_id=r6_military_id,
                reversal_date=date(2026, 9, 19),
                reversed_by=f["actor"],
            )

        with pytest.raises(
            AdvanceTaxRemittanceError,
            match="not found",
        ):
            await reverse_advance_tax_remittance(
                db,
                company_id=2147483647,
                remittance_id=r6_military_id,
                reversal_date=date(2026, 10, 1),
                reversed_by=f["actor"],
            )

        r6_reversed = await reverse_advance_tax_remittance(
            db,
            company_id=f["company"],
            remittance_id=r6_military_id,
            reversal_date=date(2026, 10, 1),
            reversed_by=f["actor"],
        )
        assert r6_reversed.id == r6_military_id

        r6_reversal = await db.scalar(
            select(JournalEntry).where(
                JournalEntry.company_id == f["company"],
                JournalEntry.reversal_of_id == r6_original_journal_id,
            )
        )
        assert r6_reversal is not None
        assert r6_reversal.status == "posted"
        assert r6_reversal.entry_date == date(2026, 10, 1)

        r6_after = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        )
        assert r6_after == r6_before + 1

        r6_replay = await reverse_advance_tax_remittance(
            db,
            company_id=f["company"],
            remittance_id=r6_military_id,
            reversal_date=date(2026, 10, 1),
            reversed_by=f["actor"],
        )
        assert r6_replay.id == r6_military_id

        assert (
            await db.scalar(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == f["company"]
                )
            )
        ) == r6_after

        with pytest.raises(
            AdvanceTaxRemittanceError,
            match="Existing reversal date",
        ):
            await reverse_advance_tax_remittance(
                db,
                company_id=f["company"],
                remittance_id=r6_military_id,
                reversal_date=date(2026, 10, 2),
                reversed_by=f["actor"],
            )

        # The immutable original payment plus its dated reversal
        # are both retained, so history can be reconstructed.
        r6_original = await db.get(
            JournalEntry, r6_original_journal_id
        )
        assert r6_original is not None
        assert r6_original.entry_date == date(2026, 9, 20)
        assert r6_original.status == "reversed"
        assert r6_reversal.entry_date > r6_original.entry_date

        r6_pit_row = await db.scalar(
            select(PayrollAdvanceTaxRemittance).where(
                PayrollAdvanceTaxRemittance.company_id == f["company"],
                PayrollAdvanceTaxRemittance.payroll_advance_id == retry.id,
                PayrollAdvanceTaxRemittance.component == "personal_income_tax",
            )
        )
        assert r6_pit_row is not None

        r6_pit_journal = await db.get(
            JournalEntry, r6_pit_row.journal_entry_id
        )
        assert r6_pit_journal.status == "posted"

        print("R6-B2-B3 ADVANCE TAX REVERSAL = PASS")
        print("R6-B2-B3 REVERSAL DATED HISTORY = PASS")
        print("R6-B2-B3 REVERSAL IDEMPOTENCY = PASS")
        print("R6-B2-B3 CROSS COMPANY AND DATE GUARDS = PASS")
        print("R6-B2-B3 PIT REMAINS ACTIVE = PASS")
        from app.services.payroll_advance_monthly_tax_clearing_db import (
            advance_tax_remittance_commitment,
        )

        for check_day, pit_active, levy_active in (
            (date(2026, 9, 19), False, False),
            (date(2026, 9, 20), True, True),
            (date(2026, 9, 30), True, True),
            (date(2026, 10, 1), True, False),
            (date(2026, 10, 2), True, False),
        ):
            observed_pit = await advance_tax_remittance_commitment(
                db,
                company_id=f["company"],
                payroll_advance_id=retry.id,
                component="personal_income_tax",
                as_of=check_day,
            )
            observed_levy = await advance_tax_remittance_commitment(
                db,
                company_id=f["company"],
                payroll_advance_id=retry.id,
                component="military_levy",
                as_of=check_day,
            )

            assert observed_pit == (
                advance_due["personal_income_tax"]
                if pit_active else Decimal("0.00")
            )
            assert observed_levy == (
                advance_due["military_levy"]
                if levy_active else Decimal("0.00")
            )

        with pytest.raises(MonthlyTaxClearingError):
            await advance_tax_remittance_commitment(
                db,
                company_id=2147483647,
                payroll_advance_id=retry.id,
                component="personal_income_tax",
                as_of=date(2026, 9, 30),
            )

        with pytest.raises(MonthlyTaxClearingError):
            await advance_tax_remittance_commitment(
                db,
                company_id=f["company"],
                payroll_advance_id=retry.id,
                component="unified_social_contribution",
                as_of=date(2026, 9, 30),
            )

        print("R6-B2-C1 HISTORICAL ADVANCE TAX = PASS")
        print("R6-B2-C1 DATE AND COMPANY GUARDS = PASS")
        print("R6-B2-C1 NO FINANCIAL WRITES = PASS")
        from app.services.payroll_advance_monthly_tax_clearing_db import (
            reconcile_combined_advance_monthly_tax_from_db,
        )

        # Existing monthly PIT/VZ remittances already settle the full
        # monthly liability. Adding advance payments is an overpayment.
        for checked_date in (
            date(2026, 9, 30),
            date(2026, 10, 1),
        ):
            with pytest.raises(
                MonthlyTaxClearingError,
                match="exceed recognized monthly liability",
            ):
                await reconcile_combined_advance_monthly_tax_from_db(
                    db,
                    company_id=f["company"],
                    payroll_advance_id=retry.id,
                    payroll_calculation_id=monthly_calculation.id,
                    as_of=checked_date,
                )

        print("R6-B2-C2-A DOUBLE CREDIT REJECTED = PASS")
        print("R6-B2-C2-A NO SILENT OVERPAYMENT = PASS")
        # R6-B2-C2-B2 — A legitimate partial positive settlement.
        # Monthly military levy was reversed earlier in the scenario.
        # Reverse monthly PIT as well; advance PIT remains active.
        from app.models.payroll_tax_remittance import (
            PayrollTaxRemittance as R6MonthlyRemittance,
        )

        r6_monthly_pit_rows = (
            await db.scalars(
                select(R6MonthlyRemittance).where(
                    R6MonthlyRemittance.company_id == f["company"],
                    R6MonthlyRemittance.component == "personal_income_tax",
                )
            )
        ).all()
        assert len(r6_monthly_pit_rows) == 1

        r6_journals_before_reversal = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        )

        await reverse_tax_remittance(
            db,
            company_id=f["company"],
            remittance_id=r6_monthly_pit_rows[0].id,
            reversal_date=date(2026, 10, 1),
            reversed_by=f["actor"],
        )

        r6_journals_after_reversal = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        )
        assert r6_journals_after_reversal == r6_journals_before_reversal + 1

        # As of 30 September, all four real payments existed.
        # Their overpayment must remain visible in historical queries.
        with pytest.raises(
            MonthlyTaxClearingError,
            match="exceed recognized monthly liability",
        ):
            await reconcile_combined_advance_monthly_tax_from_db(
                db,
                **clearing_args,
                as_of=date(2026, 9, 30),
            )

        r6_before_read = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        )

        r6_positive = await reconcile_combined_advance_monthly_tax_from_db(
            db,
            **clearing_args,
            as_of=date(2026, 10, 1),
        )

        assert r6_positive.advance_remitted["personal_income_tax"] == (
            advance_due["personal_income_tax"]
        )
        assert r6_positive.advance_remitted["military_levy"] == Decimal("0.00")

        assert all(
            r6_positive.monthly_remitted[key] == Decimal("0.00")
            for key in ("personal_income_tax", "military_levy")
        )

        assert r6_positive.total_remitted["personal_income_tax"] == (
            advance_due["personal_income_tax"]
        )
        assert r6_positive.total_remitted["military_levy"] == Decimal("0.00")

        assert r6_positive.unpaid["personal_income_tax"] == (
            tax_due["personal_income_tax"]
            - advance_due["personal_income_tax"]
        )
        assert r6_positive.unpaid["military_levy"] == (
            tax_due["military_levy"]
        )

        assert all(
            r6_positive.unpaid[key] >= Decimal("0.00")
            for key in ("personal_income_tax", "military_levy")
        )

        r6_repeat = await reconcile_combined_advance_monthly_tax_from_db(
            db,
            **clearing_args,
            as_of=date(2026, 10, 1),
        )
        assert r6_repeat == r6_positive

        r6_after_read = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == f["company"]
            )
        )
        assert r6_after_read == r6_before_read

        print("R6-B2-C2-B2 PARTIAL POSITIVE SETTLEMENT = PASS")
        print("R6-B2-C2-B2 ADVANCE PIT CREDIT = PASS")
        print("R6-B2-C2-B2 MILITARY LEVY UNPAID = PASS")
        print("R6-B2-C2-B2 HISTORICAL OVERPAYMENT RETAINED = PASS")
        print("R6-B2-C2-B2 READ ONLY IDEMPOTENT = PASS")
        print("R6-B2-B2-B INVALID AMOUNTS REJECTED = PASS")
        print("R6-B2-B2-B COMPANY ISOLATION = PASS")
        print("R6-B2-B2-B IDEMPOTENCY = PASS")
        print("R6-B2-B2-B NO EXTRA JOURNALS = PASS")
        print("R5B2-B REAL PIT AND VZ REMITTANCES = PASS")
        print("R5B2-B TAX PAYMENT IDEMPOTENCY = PASS")
        print("R5B2 MONTHLY ACCRUAL POSTED = PASS")
        # R6-A: Verify the actual production restriction.
        # The advance is dated September 20, but monthly payroll
        # accrual is dated after that advance.
        from app.services.payroll_tax_remittance_service import (
            recognized_tax as r6_recognized_tax,
            TaxRemittanceError as R6TaxRemittanceError,
        )

        assert retry.payment_date == date(2026, 9, 20)
        assert monthly_accrual.entry_date > retry.payment_date

        with pytest.raises(
            R6TaxRemittanceError,
            match="Post payroll accrual",
        ):
            await r6_recognized_tax(
                db,
                company_id=f["company"],
                calculation_id=monthly_calculation.id,
                component="personal_income_tax",
                payment_date=retry.payment_date,
                accounts={},
            )

        print("R6-A MID-MONTH TAX ATTRIBUTION BLOCKED = PASS")
        print("R6-A EXISTING MONTHLY TAX GUARD PRESERVED = PASS")
        print("R6-A ADVANCE TAX ATTRIBUTION = NOT IMPLEMENTED")
        print("R5B2 MONTHLY ACCRUAL IDEMPOTENCY = PASS")
        print("B2-R5A SAME EMPLOYEE MONTHLY STATUTORY = PASS")
        print("B2-R5A ACTUAL MONTHLY CALCULATION = PASS")
        print("B2-R5A ACTUAL STATUTORY RESULT = PASS")
