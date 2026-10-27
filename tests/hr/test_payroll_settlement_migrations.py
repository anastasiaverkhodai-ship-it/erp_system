import importlib.util
import os
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic import op
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from app.core.database import Base
from app.models.payroll_advance_tax_remittance import PayrollAdvanceTaxRemittance
from sqlalchemy.schema import AddConstraint, DropConstraint
from test_employee_foundation import employee_engine

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')]


async def test_settlement_migrations_roundtrip(employee_engine):
    modules = []
    for filename in ('b082b6582130_add_payroll_advance_foundation.py',
                     '83d21036fabe_repair_payroll_advance_journal_.py',
                     '383a1d9fd6a6_add_payroll_deduction_accounting_.py',
                     '13d4a7b8c009_payroll_advance_bank_reconciliation.py',
                     '13d4a7b8c010_payroll_advance_snapshot_and_individual_rates.py',
                     '13d4a7b8c011_employee_tax_benefit_limits.py'):
        spec = importlib.util.spec_from_file_location(filename, Path('alembic/versions') / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    spec = importlib.util.spec_from_file_location('commissioning', Path('alembic/versions/8e124c09a671_harden_fixed_asset_commissioning_source.py'))
    commissioning = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(commissioning)
    async with employee_engine.begin() as conn:
        def roundtrip(sync):
            with Operations.context(MigrationContext.configure(sync)):
                # Metadata's current cross-source check references the new column;
                # remove and restore that dependency around the older revision.
                # Revision 030 adds this dependent FK after the legacy revisions
                # exercised here. Suspend only that dependency in the empty test
                # schema, then restore it and compare the complete metadata.
                advance_fk = next(c for c in Base.metadata.tables[
                    'payroll_withholding_batches'].foreign_key_constraints
                    if c.name == 'fk_pwb_advance')
                sync.execute(DropConstraint(advance_fk, isolate_from_table=False))

                # Revision c043 adds a FK to payroll_advances. Older
                # migrations temporarily drop/recreate payroll_advances.
                # Suspend only this dependency inside the test transaction.
                advance_tax_fk = next(
                    c for c in Base.metadata.tables[
                        'payroll_advance_tax_remittances'
                    ].foreign_key_constraints
                    if c.name == 'fk_advance_tax_remittance_advance'
                )
                sync.execute(
                    DropConstraint(
                        advance_tax_fk,
                        isolate_from_table=False,
                    )
                )
                commissioning.downgrade()
                for module in reversed(modules):
                    module.downgrade()
                for module in modules:
                    module.upgrade()
                commissioning.upgrade()
                # Restore the later advance columns/checks after the legacy
                # table recreation, without weakening the final drift assertion.
                op.add_column('payroll_advances', sa.Column('withheld_amount',
                    sa.Numeric(18, 2), nullable=False, server_default='0'))
                for name in ('ck_payroll_advances_paid_gte_minimum',
                             'ck_payroll_advances_paid_lte_calculated'):
                    op.drop_constraint(name, 'payroll_advances', type_='check')
                for constraint in Base.metadata.tables['payroll_advances'].constraints:
                    if isinstance(constraint, sa.CheckConstraint) and 'withheld_amount' in str(constraint.sqltext):
                        sync.execute(AddConstraint(constraint, isolate_from_table=False))
                sync.execute(AddConstraint(advance_fk, isolate_from_table=False))
                sync.execute(
                    AddConstraint(
                        advance_tax_fk,
                        isolate_from_table=False,
                    )
                )
            differences = compare_metadata(MigrationContext.configure(sync), Base.metadata)
            assert differences == [], repr(differences)
        await conn.run_sync(roundtrip)


async def assert_history_preserved(db, filename):
    from sqlalchemy.exc import DBAPIError
    spec = importlib.util.spec_from_file_location(filename, Path('alembic/versions') / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def downgrade(sync):
        with Operations.context(MigrationContext.configure(sync)):
            module.downgrade()
    with pytest.raises(DBAPIError, match='history must be preserved'):
        async with db.begin_nested():
            await (await db.connection()).run_sync(downgrade)
