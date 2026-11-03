"""Real PostgreSQL negative evidence test on disposable ERP copy."""

import os

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import settings
from app.services.payroll_severance_corrected_tax_evidence import (
    CorrectedSeveranceTaxEvidenceError,
    verify_corrected_severance_tax_evidence,
)


@pytest.mark.asyncio
async def test_missing_company_proposal_fails_on_real_postgresql():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    engine = create_async_engine(settings.database_url)
    try:
        session_factory = async_sessionmaker(
            engine, expire_on_commit=False
        )
        async with session_factory() as db:
            with pytest.raises(
                CorrectedSeveranceTaxEvidenceError,
                match="Immutable financial proposal",
            ):
                await verify_corrected_severance_tax_evidence(
                    db,
                    company_id=2147483000,
                    financial_proposal_id=2147483000,
                    corrected_calculation_id=2147483000,
                )
    finally:
        await engine.dispose()
