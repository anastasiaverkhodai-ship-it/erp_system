import os
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_deduction_execution_postgresql import seed
from app.models.payroll_statutory import PayrollStatutoryResult
from app.services.payroll_regulatory_readiness_service import payroll_regulatory_readiness
from app.services.payroll_regulatory_report_service import (
    create_payroll_regulatory_report, generate_payroll_regulatory_report,
    PayrollRegulatoryReportError, PayrollRegulatoryReportNotFoundError,
)
from sqlalchemy import select

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv('RUN_POSTGRES_E2E') != '1', reason='Isolated PostgreSQL')]


async def test_actual_sources_and_legacy_generated_report_are_not_accepted(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, user, contract, calc, bank, beneficiary, claim = await seed(db)
        result = await db.scalar(select(PayrollStatutoryResult).where(
            PayrollStatutoryResult.company_id == company.id,
            PayrollStatutoryResult.payroll_calculation_id == calc.id))
        report = await create_payroll_regulatory_report(db, company_id=company.id,
            payroll_period_id=calc.payroll_period_id, report_kind='4df', created_by=user.id)
        readiness = await payroll_regulatory_readiness(db, company_id=company.id,
            payroll_period_id=calc.payroll_period_id, report_kind='4df')
        source = readiness['calculation_sources'][0]
        assert source['gross_amount'] == result.gross_amount > Decimal(0)
        assert source['net_amount'] == result.net_amount
        assert source['employee_withholding_amount'] == result.employee_withholding_amount
        assert source['employee_id'] == contract.employee_id
        assert source['components']
        assert all(item['source_line_id'] for item in source['components'])
        assert not readiness['ready']
        # Historical metadata must not make the old identifiers-only report valid.
        report.status = 'generated'
        report.generated_at = datetime.now(timezone.utc)
        report.generated_by = user.id
        await db.flush()
        with pytest.raises(PayrollRegulatoryReportError, match='incomplete'):
            await generate_payroll_regulatory_report(db, company_id=company.id,
                payroll_regulatory_report_id=report.id, generated_by=user.id)
        with pytest.raises(PayrollRegulatoryReportNotFoundError):
            await payroll_regulatory_readiness(db, company_id=company.id,
                payroll_period_id=calc.payroll_period_id + 1000000, report_kind='d1')
        with pytest.raises(PayrollRegulatoryReportError, match='Unsupported'):
            await payroll_regulatory_readiness(db, company_id=company.id,
                payroll_period_id=calc.payroll_period_id, report_kind='invalid')
        d6 = await payroll_regulatory_readiness(db, company_id=company.id,
            payroll_period_id=calc.payroll_period_id, report_kind='d6')
        assert d6['applicability'] == 'not_determined'
        assert 'D6_APPLICABILITY_AND_SPECIAL_SERVICE_EVIDENCE_MISSING' in d6['blocking_issues']
