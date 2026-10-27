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


async def test_two_sessions_cannot_allocate_same_journal_across_tax_services(employee_engine):
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


        # Dedicated B2 R2-D isolated PostgreSQL concurrency case.
        # All source documents must be committed before workers start.
        import asyncio
        from app.models.payroll_tax_remittance import (
            PayrollTaxRemittance,
        )
        from app.models.payroll_advance_tax_remittance import (
            PayrollAdvanceTaxRemittance,
        )
        from app.services.payroll_tax_remittance_service import (
            TaxRemittanceError,
        )
        from app.services.payroll_advance_tax_remittance_service import (
            register_advance_tax_remittance,
            AdvanceTaxRemittanceError,
        )
        from app.services.payroll_advance_tax_source_proof import (
            verify_automatic_advance_tax_source_in_db,
        )

        race_proof = await verify_automatic_advance_tax_source_in_db(
            db, retry
        )
        matches = [
            Decimal(item.amount)
            for item in race_proof.components
            if item.component == "personal_income_tax"
        ]
        assert len(matches) == 1

        race_amount = matches[0]
        assert Decimal("0") < race_amount <= tax_due[
            "personal_income_tax"
        ]

        # The source payroll accrual must precede a same-day
        # bank payment. This date change exists only in the
        # disposable, independent E2E test schema.
        assert retry.payment_date == date(2026, 9, 20)
        monthly_accrual.entry_date = retry.payment_date
        await db.flush()

        race_journal = await create_posted_tax_payment(
            db,
            type("CompanyRef", (), {"id": f["company"]})(),
            type("UserRef", (), {"id": f["actor"]})(),
            tax_accounts,
            bank,
            race_amount,
            component="personal_income_tax",
            day=retry.payment_date,
        )

        race_request = make_tax_payment_request(
            race_journal,
            bank,
            monthly_calculation,
            race_amount,
            "b2-r2d-monthly-race",
            "personal_income_tax",
        )

        advance_arguments = dict(
            company_id=f["company"],
            payroll_advance_id=retry.id,
            journal_entry_id=race_journal.id,
            bank_account_id=bank.id,
            component="personal_income_tax",
            amount=race_amount,
            request_key="b2-r2d-advance-race",
            document_reference="Real concurrent advance tax E2E",
            budget_payment_reference="Verified budget destination",
            created_by=f["actor"],
        )

        company_id = f["company"]
        actor_id = f["actor"]
        journal_id = race_journal.id

        # PostgreSQL MVCC requires committed source fixtures:
        # two new connections must see the exact same journal.
        await db.commit()
        print("R2-D COMMITTED SHARED SOURCE FIXTURES = PASS")

        async with AsyncSession(
            employee_engine, expire_on_commit=False
        ) as check:
            assert await check.scalar(
                select(JournalEntry.id).where(
                    JournalEntry.id == journal_id,
                    JournalEntry.company_id == company_id,
                    JournalEntry.status == "posted",
                )
            ) == journal_id
            await check.rollback()

        ready = 0
        start = asyncio.Event()
        ready_lock = asyncio.Lock()

        async def compete(kind):
            nonlocal ready

            async with AsyncSession(
                employee_engine, expire_on_commit=False
            ) as session:
                async with ready_lock:
                    ready += 1
                    if ready == 2:
                        start.set()

                await start.wait()

                try:
                    if kind == "monthly":
                        await register_actual_tax_payment(
                            session,
                            company_id=company_id,
                            data=race_request,
                            created_by=actor_id,
                        )
                    else:
                        await register_advance_tax_remittance(
                            session,
                            **advance_arguments,
                        )

                    await session.commit()
                    return kind, "accepted", ""

                except (
                    TaxRemittanceError,
                    AdvanceTaxRemittanceError,
                ) as exc:
                    await session.rollback()
                    return kind, "rejected", str(exc)

        results = await asyncio.wait_for(
            asyncio.gather(
                compete("monthly"),
                compete("advance"),
            ),
            timeout=60,
        )

        accepted = [
            item for item in results
            if item[1] == "accepted"
        ]
        rejected = [
            item for item in results
            if item[1] == "rejected"
        ]

        assert len(accepted) == 1, results
        assert len(rejected) == 1, results

        loser = rejected[0]
        assert (
            (
                loser[0] == "monthly"
                and "Payment journal already attributed to advance tax"
                in loser[2]
            )
            or (
                loser[0] == "advance"
                and "Bank journal already allocated to monthly tax"
                in loser[2]
            )
        ), results

        async with AsyncSession(
            employee_engine, expire_on_commit=False
        ) as check:
            monthly_rows = await check.scalar(
                select(func.count(PayrollTaxRemittance.id)).where(
                    PayrollTaxRemittance.company_id == company_id,
                    PayrollTaxRemittance.journal_entry_id == journal_id,
                )
            )
            advance_rows = await check.scalar(
                select(func.count(PayrollAdvanceTaxRemittance.id)).where(
                    PayrollAdvanceTaxRemittance.company_id == company_id,
                    PayrollAdvanceTaxRemittance.journal_entry_id == journal_id,
                )
            )

            journal_rows = await check.scalar(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.id == journal_id,
                )
            )

            assert monthly_rows + advance_rows == 1
            assert journal_rows == 1
            assert (
                (monthly_rows, advance_rows)
                in ((1, 0), (0, 1))
            )

            await check.rollback()

        print("R2-D TWO INDEPENDENT TRANSACTIONS = PASS")
        print("R2-D EXACTLY ONE ATTRIBUTION = PASS")
        print("R2-D LOSING TRANSACTION ROLLBACK = PASS")
        print("R2-D NO DUPLICATE BANK JOURNAL = PASS")
        print("R2-D REAL CROSS-SERVICE CONCURRENCY = PASS")
