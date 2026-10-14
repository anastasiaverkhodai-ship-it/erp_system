"""Statement-level attacks must not bypass payroll membership sealing."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from tests.hr.test_payroll_insert_freeze_postgresql import (
    isolated_url, migrated, seed,
)
from app.services.payroll_calculation_service import calculate_payroll_input


@pytest.mark.asyncio
@pytest.mark.parametrize('tables', [
    'payroll_input_salary_slices',
    'payroll_calculation_lines',
    'payroll_input_salary_slices, payroll_calculation_lines',
])
async def test_truncate_cannot_erase_frozen_membership(tables):
    engine = create_async_engine(isolated_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            args, slice_id = await seed(db)
            calculation = await calculate_payroll_input(db, **args)
            # SAVEPOINT protects the isolated fixture even if the attack succeeds.
            with pytest.raises(IntegrityError, match='membership cannot be truncated'):
                async with db.begin_nested():
                    await db.execute(text(f'TRUNCATE {tables} CASCADE'))
            assert await db.scalar(text(
                'SELECT count(*) FROM payroll_input_salary_slices WHERE id=:id'
            ), {'id': slice_id}) == 1
            assert await db.scalar(text(
                'SELECT count(*) FROM payroll_calculation_lines WHERE payroll_calculation_id=:id'
            ), {'id': calculation.id}) == 2
            await db.rollback()
    finally:
        await engine.dispose()
