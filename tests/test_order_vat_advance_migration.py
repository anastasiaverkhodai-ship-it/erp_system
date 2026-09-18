"""The actual migration restores all advance tables and source exclusivity."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from alembic.migration import MigrationContext
from alembic.operations import Operations
import app.models
from app.core.config import settings
from app.core.database import Base


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='Set RUN_POSTGRES_E2E=1')
async def test_actual_advance_migration_round_trip():
    path = next((Path(__file__).parents[1] / 'alembic/versions').glob('f3bac6e8a157*.py'))
    spec = importlib.util.spec_from_file_location('advance_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    schema = 'test_advance_migration_' + uuid4().hex
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.run_sync(Base.metadata.create_all)

                # This test exercises the historical f3bac6e8a157
                # order-advance migration directly. Base.metadata is the
                # current application metadata and therefore also creates
                # later 10.6 and 10.7 tables. Those tables did not exist at
                # the historical migration boundary. The later tax-invoice
                # and RK tables carry FKs across that boundary and would make
                # the old downgrade impossible for reasons unrelated to the
                # historical migration itself.
                #
                # Remove only those later objects from this isolated test
                # schema before cycling the historical migration. Keep this
                # explicit and do not use CASCADE so unexpected dependencies
                # still fail closed.
                await conn.execute(text("ALTER TABLE journal_entries DROP CONSTRAINT fk_je_rk_line"))
                await conn.execute(
                    text(
                        """
                        DROP TABLE
                            tax_invoice_correction_registration_events,
                            tax_invoice_correction_lines,
                            tax_invoice_corrections,
                            tax_invoice_registration_events,
                            tax_invoice_credit_evidence_links,
                            tax_invoice_lines,
                            tax_invoices,
                            product_tax_classifications
                        """
                    )
                )
                def cycle(sync_conn):
                    with Operations.context(MigrationContext.configure(sync_conn)):
                        migration.downgrade(); migration.upgrade()
                        migration.downgrade(); migration.upgrade()
                await conn.run_sync(cycle)
                definition = await conn.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace=CAST(:s AS regnamespace) AND conname='ck_tax_recognition_events_at_most_one_recognition_source'"), {'s': schema})
                assert 'order_vat_advance_id' in definition and 'num_nonnulls' in definition
                assert await conn.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema=:s AND table_name LIKE 'order_vat_advance%'"), {'s': schema}) == 3
            finally:
                await tx.rollback()
            assert await conn.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=:s'), {'s': schema}) == 0
    finally:
        await engine.dispose()
