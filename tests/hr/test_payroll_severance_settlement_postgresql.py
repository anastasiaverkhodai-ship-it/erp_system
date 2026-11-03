"""Real migrated PostgreSQL: termination source through final bank settlement."""
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.bank_account import BankAccount
from app.models.employee import Employee
from app.models.journal_entry import JournalEntry
from app.models.payroll_disbursement import PayrollDisbursement
from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
from app.services.payroll_disbursement_service import (
    create_payroll_disbursement, generate_and_post_payroll_disbursement_journal,
    reverse_payroll_disbursement_journal, PayrollDisbursementSourceStateError,
)
from app.services.payroll_payslip_service import generate_payroll_payslip
from app.services.payroll_employee_balance_service import employee_payroll_balance
from test_payroll_severance_snapshot_service_postgresql import _run_severance_snapshot_service_e2e


async def finance(db, company, user, employee, contract, calc, tax, *, deductions, advance_amount=D(0)):
    from calendar import monthrange
    month = contract.end_date.month
    last_day = monthrange(2026, month)[1]
    db.add(AccountingPeriod(company_id=company.id,year=2026,month=month,start_date=date(2026,month,1),
        end_date=date(2026,month,last_day),status='open',is_locked=False))
    accounts={}
    for code in ('92','94','378','66','64','65','311'):
        kind='asset' if code in ('378','311') else 'expense' if code in ('92','94') else 'liability'
        row=Account(company_id=company.id,code=code,name='Payroll '+code,account_type=kind,
            normal_balance='credit' if kind=='liability' else 'debit',is_postable=True,is_system=True,is_active=True)
        db.add(row);await db.flush();accounts[code]=row.id
    bank=BankAccount(company_id=company.id,name='Payroll bank',account_number='settlement-bank',currency_code='UAH',
        accounting_account_id=accounts['311'],is_active=True)
    db.add(bank)
    current_employee=await db.get(Employee,employee.id)
    current_employee.payment_iban='UA213223130000026007233566001'
    await db.flush()
    if advance_amount:
        # Existing historical advance, created before this final calculation.
        from app.models.payroll_advance import PayrollAdvance
        from app.services.payroll_advance_service import generate_and_post_payroll_advance_journal
        advance=PayrollAdvance(company_id=company.id,payroll_period_id=calc.payroll_period_id,
            employment_contract_id=contract.id,bank_account_id=bank.id,advance_percentage=100,
            calculation_base_amount=advance_amount,calculated_amount=advance_amount,minimum_due_amount=0,
            paid_amount=advance_amount,currency_code='UAH',payment_date=date(2026,month,10),created_by=user.id)
        db.add(advance);await db.flush()
        await generate_and_post_payroll_advance_journal(db,company_id=company.id,payroll_advance_id=advance.id,created_by=user.id)
    args=dict(company_id=company.id,payroll_calculation_id=calc.id,created_by=user.id)
    journal=await generate_and_post_payroll_journal_entry(db,**args)
    assert journal.entry_date==contract.end_date
    assert sum(x.debit for x in journal.lines)==sum(x.credit for x in journal.lines)==D('13268.80')
    assert (await generate_and_post_payroll_journal_entry(db,**args)).id==journal.id
    from test_payroll_tax_remittance import payment as tax_payment, request as tax_request
    from app.services.payroll_tax_remittance_service import register_tax_remittance
    from app.services.payroll_tax_balance_service import payroll_tax_balances
    for component,amount in [('personal_income_tax',D('2347.20')),('military_levy',D('652')),('unified_social_contribution',D('228.80'))]:
        tax_journal=await tax_payment(db,company,user,accounts,bank,amount,component,day=contract.end_date)
        data=tax_request(tax_journal,bank,calc,amount,component,component)
        remitted=await register_tax_remittance(db,company_id=company.id,data=data,created_by=user.id)
        assert (await register_tax_remittance(db,company_id=company.id,data=data,created_by=user.id)).id==remitted.id
    taxes=await payroll_tax_balances(db,company_id=company.id,as_of=contract.end_date)
    assert all(x['outstanding_amount']==0 for x in taxes['totals'])

    # Review-only allocation using actual PostgreSQL statutory totals.
    # Illustrative amounts do not grant statutory or payment approval.
    from sqlalchemy import select
    from app.models.payroll_statutory import (
        PayrollStatutoryResult,
        PayrollStatutoryResultLine,
    )
    from app.services.payroll_severance_dated_tax_allocation import (
        ComponentAllocation,
        SeveranceAllocationError,
        build_dated_severance_allocation_review,
    )

    statutory_header = await db.scalar(
        select(PayrollStatutoryResult).where(
            PayrollStatutoryResult.company_id == company.id,
            PayrollStatutoryResult.payroll_calculation_id == calc.id,
        )
    )
    assert statutory_header is not None

    component_rows = list((
        await db.scalars(
            select(PayrollStatutoryResultLine).where(
                PayrollStatutoryResultLine.company_id == company.id,
                PayrollStatutoryResultLine.payroll_statutory_result_id
                == statutory_header.id,
            )
        )
    ).all())

    totals = {line.component: D(line.amount) for line in component_rows}
    assert len(totals) == 3
    assert len(component_rows) == 3

    # Test values illustrate arithmetic reconciliation, not legal attribution.
    illustrative = {
        "personal_income_tax": D("1800.00"),
        "military_levy": D("500.00"),
        "unified_social_contribution": D("0.00"),
    }

    allocation_rows = [
        ComponentAllocation(
            component=component,
            statutory_total=total,
            ordinary_amount=total - illustrative[component],
            severance_amount=illustrative[component],
            evidence_reference="PG-E2E-ILLUSTRATIVE-NOT-APPROVED",
        )
        for component, total in totals.items()
    ]

    review = build_dated_severance_allocation_review(
        company_id=company.id,
        payroll_calculation_id=calc.id,
        income_date=contract.end_date,
        currency_code="UAH",
        rows=allocation_rows,
    )

    assert review.reconciled
    assert not review.independent_evidence_approved
    assert not review.financial_execution_authorized
    assert not review.statutory_correction_authorized

    corrupted = list(allocation_rows)
    first = corrupted[0]
    corrupted[0] = ComponentAllocation(
        component=first.component,
        statutory_total=first.statutory_total,
        ordinary_amount=first.ordinary_amount,
        severance_amount=first.severance_amount + D("0.01"),
        evidence_reference=first.evidence_reference,
    )
    with pytest.raises(
        SeveranceAllocationError,
        match="do not reconcile",
    ):
        build_dated_severance_allocation_review(
            company_id=company.id,
            payroll_calculation_id=calc.id,
            income_date=contract.end_date,
            currency_code="UAH",
            rows=corrupted,
        )

    print("REAL POSTGRESQL DATED ALLOCATION RECONCILIATION = PASS")

    from dataclasses import replace
    from app.services.payroll_severance_mixed_financial_snapshot import (
        load_mixed_severance_financial_preview,
        SeveranceFinancialSourceError,
    )

    mixed_preview = await load_mixed_severance_financial_preview(
        db,
        company_id=company.id,
        payroll_calculation_id=calc.id,
        review=review,
    )

    # Independent payroll-line evidence for each parametrized case.
    from app.models.payroll import PayrollCalculationLine

    actual_income_lines = list((
        await db.scalars(
            select(PayrollCalculationLine).where(
                PayrollCalculationLine.company_id == company.id,
                PayrollCalculationLine.payroll_calculation_id == calc.id,
            )
        )
    ).all())

    expected_severance = sum(
        (
            D(item.amount)
            for item in actual_income_lines
            if item.line_type == "severance"
        ),
        D("0.00"),
    )

    expected_ordinary = sum(
        (
            D(item.amount)
            for item in actual_income_lines
            if item.line_type != "severance"
        ),
        D("0.00"),
    )

    assert expected_severance > D("0.00")
    assert (
        expected_severance + expected_ordinary
        == D(calc.gross_amount)
    )

    assert mixed_preview.financial.gross == expected_severance
    assert mixed_preview.ordinary_gross == expected_ordinary

    # Illustrative tax amounts must match the review, not a guessed
    # percentage of severance gross.
    assert (
        mixed_preview.financial.personal_income_tax
        == review.component("personal_income_tax").severance_amount
    )
    assert (
        mixed_preview.financial.military_levy
        == review.component("military_levy").severance_amount
    )
    assert (
        mixed_preview.financial.employer_contribution
        == review.component(
            "unified_social_contribution"
        ).severance_amount
    )

    assert mixed_preview.financial.net == (
        expected_severance
        - mixed_preview.financial.personal_income_tax
        - mixed_preview.financial.military_levy
    )

    assert mixed_preview.severance_source_ids == tuple(
        item.source_termination_severance_id
        for item in actual_income_lines
        if item.line_type == "severance"
    )
    assert mixed_preview.statutory_totals_reconciled

    assert not mixed_preview.allocation_evidence_approved
    assert not mixed_preview.payment_authorized
    assert not mixed_preview.tax_correction_authorized
    assert not mixed_preview.journal_posting_authorized

    with pytest.raises(
        SeveranceFinancialSourceError,
        match="does not belong",
    ):
        await load_mixed_severance_financial_preview(
            db,
            company_id=company.id,
            payroll_calculation_id=calc.id,
            review=replace(
                review,
                payroll_calculation_id=calc.id + 100000,
            ),
        )

    print("REAL POSTGRESQL MIXED SEVERANCE SNAPSHOT = PASS")

    from types import SimpleNamespace
    from app.services.payroll_severance_mixed_financial_snapshot import (
        verify_statutory_financial_integrity,
    )

    assert verify_statutory_financial_integrity(
        gross_amount=D(calc.gross_amount),
        statutory_header=statutory_header,
        component_totals=totals,
    )

    damaged_header = SimpleNamespace(
        gross_amount=statutory_header.gross_amount,
        employee_withholding_amount=(
            D(statutory_header.employee_withholding_amount)
            + D("0.01")
        ),
        employer_contribution_amount=(
            statutory_header.employer_contribution_amount
        ),
        net_amount=statutory_header.net_amount,
    )

    with pytest.raises(
        SeveranceFinancialSourceError,
        match="withholding does not reconcile",
    ):
        verify_statutory_financial_integrity(
            gross_amount=D(calc.gross_amount),
            statutory_header=damaged_header,
            component_totals=totals,
        )

    print("REAL POSTGRESQL STATUTORY HEADER INTEGRITY = PASS")

    print("UNAPPROVED FINANCIAL EXECUTION = BLOCKED")

    print("UNVERIFIED TAX ALLOCATION FINANCIAL EXECUTION = BLOCKED")

    if advance_amount > D('800.80'):
        with pytest.raises(PayrollDisbursementSourceStateError,match='Advance offset cannot consume protected severance'):
            await create_payroll_disbursement(db,bank_account_id=bank.id,payment_date=contract.end_date,**args)
        return
    if deductions:
        from test_payroll_deduction_execution_postgresql import authorize, request as deduction_request
        def request(*args, **kwargs):
            return deduction_request(*args, **kwargs).model_copy(update={"payment_date": date(2026,month,last_day)})
        from app.services.payroll_deduction_execution_service import execute_payment
        await authorize(db,company,user,contract,D('1000'),migrated=True)
        # Caller supplies zero protection; source-derived severance overrides it.
        first=await execute_payment(db,company_id=company.id,actor_id=user.id,
            data=request(calc,bank,'protected-first',amount=D('5000')).model_copy(update={'payment_date':contract.end_date}))
        assert first.protected_amount==5000 and first.withheld_amount==0
        final_request=request(calc,bank,'protected-final',amount=D('5040.80'))
        final=await execute_payment(db,company_id=company.id,actor_id=user.id,data=final_request)
        assert final.protected_amount==D('4240')
        assert final.withheld_amount==D('160.16') # only ordinary net 800.80 * 20%
        assert (await execute_payment(db,company_id=company.id,actor_id=user.id,data=final_request)).id==final.id
    else:
        payment=await create_payroll_disbursement(db,bank_account_id=bank.id,payment_date=contract.end_date,**args)
        assert payment.amount==tax.net_amount-advance_amount==D('10040.80')-advance_amount
        posting=await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=payment.id,created_by=user.id)
        assert sum(x.debit for x in posting.lines)==D('10040.80')-advance_amount

    from app.services.payroll_severance_postpayment_assessment import (
        assess_severance_postpayment,
    )

    posted_assessment = await assess_severance_postpayment(
        db,
        company_id=company.id,
        payroll_input_id=calc.payroll_input_id,
    )

    assert posted_assessment.posted_disbursement_count >= 1
    assert posted_assessment.posted_disbursement_total > D("0.00")
    assert posted_assessment.gross_difference == D("0.00")
    assert posted_assessment.payment_authorized is False
    assert posted_assessment.tax_correction_authorized is False
    assert posted_assessment.journal_posting_authorized is False

    print("ACTUAL POSTED SEVERANCE PAYMENT ASSESSMENT = PASS")

    from app.services.payroll_severance_postpayment_funding import (
        load_posted_payroll_funding,
    )

    # Compare like-for-like:
    # assessment includes all active posted disbursements,
    # so the dated funding query must include the final
    # end-of-month payment in split-payment scenarios.
    settlement_as_of = date(2026, month, last_day)

    funding_before = await load_posted_payroll_funding(
        db,
        company_id=company.id,
        payroll_calculation_id=calc.id,
        as_of=settlement_as_of,
    )

    assert funding_before.posted_payment_count >= 1
    if deductions:
        assert funding_before.posted_payment_count >= 2
    else:
        assert funding_before.posted_payment_count == 1
    assert funding_before.posted_payment_total == (
        posted_assessment.posted_disbursement_total
    )
    assert funding_before.remitted_personal_income_tax == D("2347.20")
    assert funding_before.remitted_military_levy == D("652.00")
    assert funding_before.remitted_employer_contribution == D("228.80")

    assert not funding_before.financial_execution_authorized
    assert not funding_before.severance_tax_attribution_verified

    print("REAL POSTGRESQL POSTED FUNDING EVIDENCE = PASS")

    from app.services.payroll_severance_postpayment_position import (
        reconcile_severance_postpayment_position,
        SeverancePositionError,
    )
    from dataclasses import replace as replace_position_evidence

    position = reconcile_severance_postpayment_position(
        preview=mixed_preview,
        review=review,
        funding=funding_before,
        assessment=posted_assessment,
    )

    assert position.reconciled
    assert position.payroll_gross == D(calc.gross_amount)
    assert position.payroll_net == D(tax.net_amount)
    assert position.employee_posted == (
        posted_assessment.posted_disbursement_total
    )
    assert not position.severance_amount_executable
    assert not position.journal_posting_authorized

    with pytest.raises(
        SeverancePositionError,
        match="disagree",
    ):
        reconcile_severance_postpayment_position(
            preview=mixed_preview,
            review=review,
            funding=funding_before,
            assessment=replace_position_evidence(
                posted_assessment,
                posted_disbursement_total=(
                    posted_assessment.posted_disbursement_total
                    + D("0.01")
                ),
            ),
        )

    print("REAL POSTGRESQL SEVERANCE FUNDING POSITION = PASS")

    from app.services.payroll_severance_correction_case_service import (
        open_postpayment_severance_case,
        SeveranceCorrectionCaseError,
    )

    correction_case = await open_postpayment_severance_case(
        db,
        company_id=company.id,
        payroll_calculation_id=calc.id,
        as_of=settlement_as_of,
        evidence_date=contract.end_date,
        evidence_reference="PG-E2E-SEVERANCE-REVIEW-001",
        request_key=f"pg-severance-case-{calc.id}",
        actor_id=user.id,
    )

    assert correction_case.id > 0
    assert correction_case.status == "review_required"
    assert correction_case.original_severance_gross == (
        posted_assessment.original_gross
    )
    assert correction_case.posted_payroll_amount_at_opening == (
        funding_before.posted_payment_total
    )

    replay = await open_postpayment_severance_case(
        db,
        company_id=company.id,
        payroll_calculation_id=calc.id,
        as_of=settlement_as_of,
        evidence_date=contract.end_date,
        evidence_reference="PG-E2E-SEVERANCE-REVIEW-001",
        request_key=f"pg-severance-case-{calc.id}",
        actor_id=user.id,
    )

    assert replay.id == correction_case.id

    with pytest.raises(
        SeveranceCorrectionCaseError,
        match="different evidence",
    ):
        await open_postpayment_severance_case(
            db,
            company_id=company.id,
            payroll_calculation_id=calc.id,
            as_of=settlement_as_of,
            evidence_date=contract.end_date,
            evidence_reference="PG-E2E-CHANGED-REVIEW-002",
            request_key=f"pg-severance-case-{calc.id}",
            actor_id=user.id,
        )

    with pytest.raises(
        SeveranceCorrectionCaseError,
        match="active correction case",
    ):
        await open_postpayment_severance_case(
            db,
            company_id=company.id,
            payroll_calculation_id=calc.id,
            as_of=settlement_as_of,
            evidence_date=contract.end_date,
            evidence_reference="PG-E2E-ANOTHER-REVIEW-003",
            request_key=f"pg-severance-case-second-{calc.id}",
            actor_id=user.id,
        )

    print("REAL POSTGRESQL PERSISTED SEVERANCE CASE = PASS")

    from app.services.payroll_severance_correction_case_service import (
        require_exact_severance_case_source,
    )
    from types import SimpleNamespace

    original_severance_lines = list((
        await db.scalars(
            select(PayrollCalculationLine).where(
                PayrollCalculationLine.company_id == company.id,
                PayrollCalculationLine.payroll_calculation_id == calc.id,
                PayrollCalculationLine.line_type == "severance",
            )
        )
    ).all())

    assert require_exact_severance_case_source(
        calculation=calc,
        assessment=posted_assessment,
        severance_lines=original_severance_lines,
    ) == correction_case.original_severance_source_id

    actual_line = original_severance_lines[0]

    with pytest.raises(
        SeveranceCorrectionCaseError,
        match="source identity mismatch",
    ):
        require_exact_severance_case_source(
            calculation=calc,
            assessment=posted_assessment,
            severance_lines=[
                SimpleNamespace(
                    company_id=actual_line.company_id,
                    payroll_calculation_id=actual_line.payroll_calculation_id,
                    line_type="severance",
                    source_termination_severance_id=(
                        actual_line.source_termination_severance_id
                        + 100000
                    ),
                    amount=actual_line.amount,
                )
            ],
        )

    print("REAL POSTGRESQL CORRECTION SOURCE PINNING = PASS")

    from app.services.payroll_severance_financial_proposal_service import (
        create_severance_financial_proposal,
        SeveranceFinancialProposalError,
    )
    from app.services.payroll_severance_net_tax_settlement import (
        SeveranceFinancialSnapshot,
    )

    initial_financial = mixed_preview.financial

    proposed_financial = SeveranceFinancialSnapshot(
        gross=initial_financial.gross + D("100.00"),
        personal_income_tax=initial_financial.personal_income_tax,
        military_levy=initial_financial.military_levy,
        net=initial_financial.net + D("100.00"),
        employer_contribution=initial_financial.employer_contribution,
    )

    proposal = await create_severance_financial_proposal(
        db,
        company_id=company.id,
        correction_case_id=correction_case.id,
        original_preview=mixed_preview,
        proposed_financial=proposed_financial,
        document_reference="PG-E2E-ILLUSTRATIVE-PROPOSAL-001",
        document_date=settlement_as_of,
        request_key=f"pg-sev-proposal-{correction_case.id}",
        submitted_by=user.id,
    )

    assert proposal.id > 0
    assert proposal.status == "review_required"
    assert proposal.original_financial["gross"] == str(
        initial_financial.gross
    )
    assert proposal.proposed_financial["gross"] == str(
        proposed_financial.gross
    )

    replay = await create_severance_financial_proposal(
        db,
        company_id=company.id,
        correction_case_id=correction_case.id,
        original_preview=mixed_preview,
        proposed_financial=proposed_financial,
        document_reference="PG-E2E-ILLUSTRATIVE-PROPOSAL-001",
        document_date=settlement_as_of,
        request_key=f"pg-sev-proposal-{correction_case.id}",
        submitted_by=user.id,
    )

    assert replay.id == proposal.id

    with pytest.raises(
        SeveranceFinancialProposalError,
        match="different proposal",
    ):
        await create_severance_financial_proposal(
            db,
            company_id=company.id,
            correction_case_id=correction_case.id,
            original_preview=mixed_preview,
            proposed_financial=proposed_financial,
            document_reference="PG-E2E-DIFFERENT-DOCUMENT-002",
            document_date=settlement_as_of,
            request_key=f"pg-sev-proposal-{correction_case.id}",
            submitted_by=user.id,
        )

    with pytest.raises(
        SeveranceFinancialProposalError,
        match="already has a financial proposal",
    ):
        await create_severance_financial_proposal(
            db,
            company_id=company.id,
            correction_case_id=correction_case.id,
            original_preview=mixed_preview,
            proposed_financial=proposed_financial,
            document_reference="PG-E2E-SECOND-PROPOSAL-003",
            document_date=settlement_as_of,
            request_key=f"pg-sev-proposal-other-{correction_case.id}",
            submitted_by=user.id,
        )

    print("REAL POSTGRESQL IMMUTABLE FINANCIAL PROPOSAL = PASS")
    print("REAL POSTGRESQL PROPOSAL IDEMPOTENCY = PASS")

    # Verify the database rejects real UPDATE/DELETE statements,
    # not merely that immutable triggers have been registered.
    # Each rejected statement runs in an independent savepoint,
    # preserving the outer integration-test transaction.
    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import DBAPIError

    mutation_attempts = (
        (
            "payroll_severance_correction_cases",
            correction_case.id,
            "UPDATE",
        ),
        (
            "payroll_severance_correction_cases",
            correction_case.id,
            "DELETE",
        ),
        (
            "payroll_severance_financial_proposals",
            proposal.id,
            "UPDATE",
        ),
        (
            "payroll_severance_financial_proposals",
            proposal.id,
            "DELETE",
        ),
    )

    for table_name, row_id, operation in mutation_attempts:
        if operation == "UPDATE":
            statement = sql_text(
                f"UPDATE {table_name} "
                "SET status = 'review_required' "
                "WHERE company_id = :company_id AND id = :row_id"
            )
        else:
            statement = sql_text(
                f"DELETE FROM {table_name} "
                "WHERE company_id = :company_id AND id = :row_id"
            )

        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(
                    statement,
                    {
                        "company_id": company.id,
                        "row_id": row_id,
                    },
                )

    from app.models.payroll_severance_correction_case import (
        PayrollSeveranceCorrectionCase,
    )
    from app.models.payroll_severance_financial_proposal import (
        PayrollSeveranceFinancialProposal,
    )

    # Failed mutations must not remove the source documents.
    preserved_case = await db.scalar(
        select(PayrollSeveranceCorrectionCase).where(
            PayrollSeveranceCorrectionCase.id
            == correction_case.id,
            PayrollSeveranceCorrectionCase.company_id
            == company.id,
        )
    )
    preserved_proposal = await db.scalar(
        select(PayrollSeveranceFinancialProposal).where(
            PayrollSeveranceFinancialProposal.id
            == proposal.id,
            PayrollSeveranceFinancialProposal.company_id
            == company.id,
        )
    )

    assert preserved_case is not None
    assert preserved_proposal is not None
    assert preserved_case.status == "review_required"
    assert preserved_proposal.status == "review_required"

    print("REAL POSTGRESQL IMMUTABLE TRIGGER ENFORCEMENT = PASS")

    from app.services.payroll_severance_financial_approval_gate import (
        FinancialReviewEvidence,
        SeveranceApprovalGateError,
        financial_approval_preflight,
    )
    from types import SimpleNamespace as ReviewRecord

    original_review = SeveranceFinancialSnapshot(
        **{
            field: D(proposal.original_financial[field])
            for field in (
                "gross", "personal_income_tax",
                "military_levy", "net",
                "employer_contribution",
            )
        }
    )

    corrected_review = SeveranceFinancialSnapshot(
        **{
            field: D(proposal.proposed_financial[field])
            for field in (
                "gross", "personal_income_tax",
                "military_levy", "net",
                "employer_contribution",
            )
        }
    )

    independent_review = FinancialReviewEvidence(
        reviewer_id=user.id + 100000,
        submitter_id=user.id,
        document_reference="PG-E2E-INDEPENDENT-REVIEW-001",
        normative_source_reference="PG-E2E-SOURCE-REVIEW-001",
        reviewed_original=original_review,
        reviewed_corrected=corrected_review,
    )

    preflight = financial_approval_preflight(
        proposal=proposal,
        case=correction_case,
        evidence=independent_review,
    )

    assert preflight.financial_integrity_reconciled
    assert preflight.reviewer_separated
    assert not preflight.statutory_source_verified
    assert not preflight.journal_posting_authorized

    with pytest.raises(
        SeveranceApprovalGateError,
        match="Independent reviewer",
    ):
        financial_approval_preflight(
            proposal=proposal,
            case=correction_case,
            evidence=replace_position_evidence(
                independent_review,
                reviewer_id=user.id,
            ),
        )

    print("REAL POSTGRESQL FINANCIAL REVIEW PREFLIGHT = PASS")

    from app.services.payroll_severance_financial_delta_ledger import (
        calculate_severance_deltas,
        load_severance_financial_deltas,
        SeveranceDeltaError,
    )

    persisted_deltas = await load_severance_financial_deltas(
        db,
        company_id=company.id,
        correction_case_id=correction_case.id,
    )

    assert persisted_deltas.case_id == correction_case.id
    assert persisted_deltas.proposal_id == proposal.id
    assert persisted_deltas.reconciled
    assert persisted_deltas.gross_delta == D("100.00")
    assert persisted_deltas.net_delta == D("100.00")
    assert not persisted_deltas.statutory_source_verified
    assert not persisted_deltas.journal_posting_authorized

    with pytest.raises(
        SeveranceDeltaError,
        match="identity mismatch",
    ):
        calculate_severance_deltas(
            case=correction_case,
            proposal=ReviewRecord(
                id=proposal.id,
                company_id=company.id + 1,
                correction_case_id=correction_case.id,
                status=proposal.status,
                original_financial=proposal.original_financial,
                proposed_financial=proposal.proposed_financial,
            ),
        )

    print("REAL POSTGRESQL SOURCE-BOUND SEVERANCE DELTAS = PASS")

    from app.services.payroll_severance_adjustment_journal_service import (
        create_severance_adjustment_draft,
    )
    from app.models.payroll_severance_adjustment_journal import (
        PayrollSeveranceAdjustmentJournal,
    )
    from app.models.journal_entry import JournalEntryStatus
    from sqlalchemy import text as adjustment_sql_text
    from sqlalchemy.exc import DBAPIError as AdjustmentDBAPIError

    adjustment_journal = await create_severance_adjustment_draft(
        db,
        company_id=company.id,
        correction_case_id=correction_case.id,
        correction_date=settlement_as_of,
        actor_id=user.id,
    )

    assert adjustment_journal.id > 0
    assert adjustment_journal.status == JournalEntryStatus.DRAFT
    assert adjustment_journal.payroll_calculation_id is None
    assert len(adjustment_journal.lines) == 2

    assert sum(
        (D(row.debit) for row in adjustment_journal.lines),
        D("0.00"),
    ) == D("100.00")

    assert sum(
        (D(row.credit) for row in adjustment_journal.lines),
        D("0.00"),
    ) == D("100.00")

    replay_draft = await create_severance_adjustment_draft(
        db,
        company_id=company.id,
        correction_case_id=correction_case.id,
        correction_date=settlement_as_of,
        actor_id=user.id,
    )
    assert replay_draft.id == adjustment_journal.id

    # Real PostgreSQL must refuse any direct status transition,
    # even when a caller bypasses the Python service.
    with pytest.raises(AdjustmentDBAPIError):
        async with db.begin_nested():
            await db.execute(
                adjustment_sql_text(
                    "UPDATE journal_entries SET status = 'posted' "
                    "WHERE company_id = :company AND id = :journal"
                ),
                {
                    "company": company.id,
                    "journal": adjustment_journal.id,
                },
            )

    assert await db.scalar(
        select(PayrollSeveranceAdjustmentJournal.id).where(
            PayrollSeveranceAdjustmentJournal.company_id
            == company.id,
            PayrollSeveranceAdjustmentJournal.financial_proposal_id
            == proposal.id,
        )
    ) is not None

    print("REAL POSTGRESQL SEVERANCE ADJUSTMENT DRAFT = PASS")
    print("REAL POSTGRESQL PREMATURE POSTING BLOCKED = PASS")






    print("CASE IDEMPOTENCY + COMPETING REQUEST GUARDS = PASS")




    from app.services.payroll_severance_verified_tax_bridge import (
        load_severance_only_financial_snapshot,
        SeveranceTaxSourceError,
    )
    with pytest.raises(
        SeveranceTaxSourceError,
        match="requires independent",
    ):
        await load_severance_only_financial_snapshot(
            db,
            company_id=company.id,
            calculation_id=calc.id,
            income_date=contract.end_date,
            policies=(),
        )
    print("MIXED SEVERANCE TAX MISATTRIBUTION = BLOCKED")


    slip=await generate_payroll_payslip(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
    assert slip.gross_amount==D('13040') and slip.net_amount==D('10040.80')
    balance=await employee_payroll_balance(db,company_id=company.id,employee_id=employee.id,as_of=date(2026,month,last_day))
    assert balance['complete'] and balance['balances'][0]['balance']==0
    with pytest.raises(PayrollDisbursementSourceStateError,match='remaining'):
        if deductions:
            await execute_payment(db,company_id=company.id,actor_id=user.id,
                data=request(calc,bank,'overpay',amount=D('1')))
        else:
            await create_payroll_disbursement(db,bank_account_id=bank.id,payment_date=contract.end_date,amount=D('1'),request_key='overpay',**args)
    if deductions:
        from app.services.accounting_reversal import AccountingReversalError
        with pytest.raises(AccountingReversalError,match='later severance payments'):
            await reverse_payroll_disbursement_journal(db,company_id=company.id,
                payroll_disbursement_id=first.disbursement_id,reversal_date=date(2026,month,last_day),reversed_by=user.id)
        await reverse_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=final.disbursement_id,reversal_date=date(2026,month,last_day),reversed_by=user.id)
        if contract.end_date.day < 30:
            with pytest.raises(AccountingReversalError,match='later severance payments'):
                await reverse_payroll_disbursement_journal(db,company_id=company.id,
                    payroll_disbursement_id=first.disbursement_id,reversal_date=date(2026,month,last_day-1),reversed_by=user.id)
        replacement=await execute_payment(db,company_id=company.id,actor_id=user.id,
            data=request(calc,bank,'replacement',amount=D('5040.80')))
        assert replacement.protected_amount==D('4240') and replacement.withheld_amount==D('160.16')
        await reverse_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=replacement.disbursement_id,reversal_date=date(2026,month,last_day),reversed_by=user.id)
        await reverse_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=first.disbursement_id,reversal_date=date(2026,month,last_day),reversed_by=user.id)
    else:
        await reverse_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=payment.id,reversal_date=date(2026,month,last_day),reversed_by=user.id)
    balance=await employee_payroll_balance(db,company_id=company.id,employee_id=employee.id,as_of=date(2026,month,last_day))
    assert balance['complete'] and balance['balances'][0]['balance']==D('10040.80')-advance_amount

    reversed_assessment = await assess_severance_postpayment(
        db,
        company_id=company.id,
        payroll_input_id=calc.payroll_input_id,
    )

    assert reversed_assessment.posted_disbursement_count == 0
    assert reversed_assessment.posted_disbursement_total == D("0.00")
    assert reversed_assessment.payment_authorized is False

    print("REVERSED SEVERANCE PAYMENT ASSESSMENT = PASS")

    funding_after = await load_posted_payroll_funding(
        db,
        company_id=company.id,
        payroll_calculation_id=calc.id,
        as_of=date(2026, month, last_day),
    )

    assert funding_after.posted_payment_count == 0
    assert funding_after.posted_payment_total == D("0.00")
    assert funding_after.remitted_personal_income_tax == D("2347.20")
    assert funding_after.remitted_military_levy == D("652.00")
    assert funding_after.remitted_employer_contribution == D("228.80")

    print("REAL POSTGRESQL REVERSED FUNDING EVIDENCE = PASS")




@pytest.mark.asyncio
@pytest.mark.parametrize('deductions',[False,True])
@pytest.mark.parametrize('termination_day',[15,30])
async def test_severance_final_settlement(deductions, termination_day):
    async def callback(*args):
        await finance(*args,deductions=deductions)
    await _run_severance_snapshot_service_e2e(exercise_financial_path=True,trusted_policy=True,financial_callback=callback,termination_day=termination_day)


@pytest.mark.asyncio
async def test_severance_classification_http_permission_retry_and_company_scope():
    async def callback(db,company,user,employee,contract,calc,tax):
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport
        from types import SimpleNamespace
        from app.api.v1.payroll_statutory import router
        from app.api.deps import get_current_user
        from app.core.database import get_db
        from app.models.role import Role
        from app.models.permission import Permission
        from app.models.rbac import role_permissions
        from app.models.user_company_role import UserCompanyRole
        api=FastAPI();api.include_router(router)
        async def session(): yield db
        api.dependency_overrides[get_db]=session
        api.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=user.id)
        url=f'/companies/{company.id}/payroll-calculations/{calc.id}/severance-classification'
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            assert (await client.post(url)).status_code==403
            role=Role(name=f'severance-http-{company.id}');db.add(role);await db.flush()
            permission=await db.scalar(select(Permission).where(Permission.name=='employees.manage'))
            if permission is None:
                permission=Permission(name='employees.manage');db.add(permission);await db.flush()
            await db.execute(role_permissions.insert(),dict(role_id=role.id,permission_id=permission.id))
            db.add(UserCompanyRole(company_id=company.id,user_id=user.id,role_id=role.id,is_active=True))
            await db.flush()
            first=await client.post(url)
            assert first.status_code==200,first.text
            assert first.json()['income_code_4df']=='127'
            second=await client.post(url)
            assert second.status_code==200 and second.json()==first.json()
            assert (await client.post(url.replace(f'/companies/{company.id}/','/companies/2147483647/'))).status_code==403
    await _run_severance_snapshot_service_e2e(exercise_financial_path=True,trusted_policy=True,financial_callback=callback)


@pytest.mark.asyncio
@pytest.mark.parametrize('advance_amount',[D('300'),D('801')])
async def test_severance_advance_offset(advance_amount):
    async def callback(*args):
        await finance(*args,deductions=False,advance_amount=advance_amount)
    await _run_severance_snapshot_service_e2e(exercise_financial_path=True,trusted_policy=True,financial_callback=callback)


@pytest.mark.asyncio
async def test_current_policy_october_final_settlement():
    async def callback(*args):
        await finance(*args, deductions=True)
    await _run_severance_snapshot_service_e2e(
        exercise_financial_path=True, trusted_policy=True, financial_callback=callback,
        termination_day=22, termination_month=10,
    )
