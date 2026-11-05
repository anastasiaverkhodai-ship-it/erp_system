"""Statement-level attacks must not bypass payroll membership sealing."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
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

            # Complete deferred FK/constraint events before the
            # TRUNCATE attempt. Otherwise PostgreSQL may reject
            # the statement before the membership protection
            # trigger has an opportunity to execute.
            #
            # Keep all rows inside the outer test transaction:
            # no COMMIT, no persistent test-data leakage.
            await db.flush()
            await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))

            # PostgreSQL can reject TRUNCATE through either:
            # 1. The membership immutability trigger (23514).
            # 2. Missing TRUNCATE privilege on protected tables
            #    reached through CASCADE (42501).
            #
            # Pending trigger events (55006) are NOT accepted.
            # These are different security outcomes and must
            # be checked separately, not treated as equivalent.

            role = await db.scalar(text('SELECT current_user'))
            assert role == 'erp_user'

            can_truncate_provenance = await db.scalar(text(
                "SELECT has_table_privilege("
                "current_user, "
                "'public.payroll_statutory_line_sources', "
                "'TRUNCATE')"
            ))
            assert can_truncate_provenance is False

            # SAVEPOINT contains the rejected statement.
            with pytest.raises(DBAPIError) as caught:
                async with db.begin_nested():
                    await db.execute(
                        text(f'TRUNCATE {tables} CASCADE')
                    )

            sqlstate = getattr(
                caught.value.orig, 'sqlstate', None
            )
            message = str(caught.value).lower()

            if sqlstate == '23514':
                assert isinstance(caught.value, IntegrityError)
                assert 'membership cannot be truncated' in message
                print(
                    'MEMBERSHIP TRUNCATE TRIGGER = PASS',
                    tables,
                )

            elif sqlstate == '42501':
                # Only accept the known protected provenance
                # table privilege barrier, never an arbitrary
                # missing privilege on another table.
                assert tables != 'payroll_input_salary_slices'
                assert (
                    'permission denied for table '
                    'payroll_statutory_line_sources'
                ) in message
                print(
                    'PROTECTED CASCADE ACL = PASS',
                    tables,
                )

            else:
                raise AssertionError(
                    'Unexpected TRUNCATE rejection: '
                    f'SQLSTATE={sqlstate}; {caught.value}'
                )

            # A rejection is only valid if the original data
            # remains intact. Existing assertions below prove it.
            assert await db.scalar(text(
                'SELECT count(*) FROM payroll_input_salary_slices WHERE id=:id'
            ), {'id': slice_id}) == 1
            assert await db.scalar(text(
                'SELECT count(*) FROM payroll_calculation_lines WHERE payroll_calculation_id=:id'
            ), {'id': calculation.id}) == 2
            await db.rollback()
    finally:
        await engine.dispose()
