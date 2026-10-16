import os

import pytest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.models.payroll_disbursement import PayrollDisbursement
from alembic.config import Config
from alembic.script import ScriptDirectory

pytestmark=pytest.mark.skipif(os.getenv("RUN_POSTGRES_E2E")!="1",reason="Isolated PostgreSQL required")


@pytest.mark.asyncio
async def test_payroll_withheld_schema_probe():
    assert os.getenv("RUN_POSTGRES_E2E") == "1"

    from sqlalchemy.engine import make_url

    url = make_url(str(settings.database_url))

    assert (url.database or "").startswith("erp_r3_copy_")

    engine = create_async_engine(
        str(settings.database_url),
        echo=False,
    )

    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    text("SET TRANSACTION READ ONLY")
                )

                revision = await connection.scalar(
                    text(
                        "SELECT version_num FROM alembic_version"
                    )
                )

                db_columns = (
                    await connection.execute(
                        text("""
                            SELECT
                                column_name,
                                data_type,
                                is_nullable,
                                column_default
                            FROM information_schema.columns
                            WHERE table_schema = current_schema()
                              AND table_name = 'payroll_disbursements'
                            ORDER BY ordinal_position
                        """)
                    )
                ).mappings().all()

                db_names = {
                    row["column_name"]
                    for row in db_columns
                }

                orm_names = {
                    column.name
                    for column in PayrollDisbursement.__table__.columns
                }

                missing = sorted(orm_names - db_names)
                extra = sorted(db_names - orm_names)

                print()
                print("SCHEMA PROBE DATABASE =", url.database)
                print("ALEMBIC REVISION =", revision)
                print("ORM COLUMNS =", len(orm_names))
                print("DATABASE COLUMNS =", len(db_names))
                print("ORM COLUMNS MISSING IN DB =", missing)
                print("DB COLUMNS MISSING IN ORM =", extra)

                for row in db_columns:
                    if row["column_name"] == "withheld_amount":
                        print("WITHHELD COLUMN =", dict(row))

                assert revision == ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()

                assert not missing, (
                    "SCHEMA MISMATCH: "
                    f"ORM columns absent from database: {missing}"
                )

                assert "withheld_amount" in db_names

                print("SCHEMA PROBE = PASS")

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_payroll_withheld_schema_probe_after_roundtrip():
    print()
    import asyncio
    import sys
    from sqlalchemy.engine import make_url
    assert (make_url(settings.database_url).database or '').startswith('erp_r3_copy_')
    for command in (('downgrade','13d4a7b8c030'),('upgrade','head')):
        process=await asyncio.create_subprocess_exec(sys.executable,'-m','alembic',*command,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        output,_=await process.communicate()
        assert process.returncode==0,output.decode()
    print("AFTER MIGRATION ROUNDTRIP — NEW CONNECTION")
    await test_payroll_withheld_schema_probe()
    print("AFTER ROUNDTRIP SCHEMA = PASS")
