import os
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.models
from app.core.config import settings
from app.core.database import Base
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.company import Company
from app.models.fixed_asset import FixedAsset, FixedAssetStatus
from app.models.fixed_asset_repair_improvement import (
    FixedAssetRepairImprovement,
    FixedAssetRepairImprovementType,
)
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.models.user import User
from app.models import FixedAssetGroup
from app.schemas.fixed_asset_repair_improvement import (
    FixedAssetRepairImprovementCreate,
)
from app.services.fixed_asset_repair_improvement_service import (
    FixedAssetRepairImprovementError,
    create_fixed_asset_repair_improvement,
    reverse_fixed_asset_repair_improvement,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Set RUN_POSTGRES_E2E=1",
    ),
]


DAY1 = date(2026, 2, 10)
DAY2 = date(2026, 2, 11)
DAY3 = date(2026, 2, 12)


def _minimal_kwargs(model, explicit):
    values = dict(explicit)

    for column in model.__table__.columns:
        if column.name in values:
            continue
        if column.primary_key:
            continue
        if column.nullable:
            continue
        if column.default is not None:
            continue
        if column.server_default is not None:
            continue
        if column.foreign_keys:
            continue

        python_type = None
        try:
            python_type = column.type.python_type
        except Exception:
            pass

        if python_type is str:
            values[column.name] = (
                f"test-{column.name}-{uuid.uuid4().hex[:8]}"
            )
        elif python_type is bool:
            values[column.name] = True
        elif python_type is int:
            values[column.name] = 1
        elif python_type is Decimal:
            values[column.name] = Decimal("0.00")
        elif python_type is date:
            values[column.name] = date(2026, 1, 1)
        else:
            raise RuntimeError(
                f"Cannot infer required field "
                f"{model.__name__}.{column.name} "
                f"of type {column.type}"
            )

    return values


@pytest_asyncio.fixture
async def engine():
    admin = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
    )
    schema = "test_fari_" + uuid.uuid4().hex

    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={
            "server_settings": {
                "search_path": schema,
            }
        },
    )

    try:
        async with admin.begin() as conn:
            await conn.execute(
                text(f'CREATE SCHEMA "{schema}"')
            )

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        yield engine

    finally:
        await engine.dispose()

        async with admin.begin() as conn:
            await conn.execute(
                text(
                    f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'
                )
            )

        await admin.dispose()


async def seed(db):
    suffix = uuid.uuid4().hex[:10]

    company = Company(
        **_minimal_kwargs(
            Company,
            {
                "name": f"FARI company {suffix}",
            },
        )
    )
    db.add(company)
    await db.flush()

    other_company = Company(
        **_minimal_kwargs(
            Company,
            {
                "name": f"FARI other company {suffix}",
            },
        )
    )
    db.add(other_company)
    await db.flush()

    actor = User(
        **_minimal_kwargs(
            User,
            {
                "email": f"fari-{suffix}@example.test",
                "password_hash": "test-password-hash",
                "first_name": "Fixed",
                "last_name": "Asset",
            },
        )
    )
    db.add(actor)
    await db.flush()

    asset_account = Account(
        **_minimal_kwargs(
            Account,
            {
                "company_id": company.id,
                "code": f"10{suffix[:4]}",
                "name": f"Fixed assets {suffix}",
                "account_type": "asset",
                "normal_balance": "debit",
                "is_active": True,
                "is_postable": True,
            },
        )
    )

    accumulated_account = Account(
        **_minimal_kwargs(
            Account,
            {
                "company_id": company.id,
                "code": f"13{suffix[:4]}",
                "name": f"Accumulated depreciation {suffix}",
                "account_type": "asset",
                "normal_balance": "credit",
                "is_active": True,
                "is_postable": True,
            },
        )
    )

    depreciation_expense_account = Account(
        **_minimal_kwargs(
            Account,
            {
                "company_id": company.id,
                "code": f"92{suffix[:4]}",
                "name": f"Depreciation expense {suffix}",
                "account_type": "expense",
                "normal_balance": "debit",
                "is_active": True,
                "is_postable": True,
            },
        )
    )

    repair_expense_account = Account(
        **_minimal_kwargs(
            Account,
            {
                "company_id": company.id,
                "code": f"93{suffix[:4]}",
                "name": f"Repair expense {suffix}",
                "account_type": "expense",
                "normal_balance": "debit",
                "is_active": True,
                "is_postable": True,
            },
        )
    )

    nonexpense_account = Account(
        **_minimal_kwargs(
            Account,
            {
                "company_id": company.id,
                "code": f"37{suffix[:4]}",
                "name": f"Non-expense source {suffix}",
                "account_type": "asset",
                "normal_balance": "debit",
                "is_active": True,
                "is_postable": True,
            },
        )
    )

    balancing_account = Account(
        **_minimal_kwargs(
            Account,
            {
                "company_id": company.id,
                "code": f"63{suffix[:4]}",
                "name": f"Settlement {suffix}",
                "account_type": "liability",
                "normal_balance": "credit",
                "is_active": True,
                "is_postable": True,
            },
        )
    )

    db.add_all(
        [
            asset_account,
            accumulated_account,
            depreciation_expense_account,
            repair_expense_account,
            nonexpense_account,
            balancing_account,
        ]
    )
    await db.flush()

    db.add(
        AccountingPeriod(
            **_minimal_kwargs(
                AccountingPeriod,
                {
                    "company_id": company.id,
                    "year": 2026,
                    "month": 2,
                    "start_date": date(2026, 2, 1),
                    "end_date": date(2026, 2, 28),
                    "status": "open",
                    "is_locked": False,
                },
            )
        )
    )
    await db.flush()

    source_journal = JournalEntry(
        **_minimal_kwargs(
            JournalEntry,
            {
                "company_id": company.id,
                "entry_date": DAY1,
                "description": f"Repair expense source {suffix}",
                "status": JournalEntryStatus.POSTED,
                "created_by": actor.id,
            },
        )
    )
    db.add(source_journal)
    await db.flush()

    source_line = JournalEntryLine(
        **_minimal_kwargs(
            JournalEntryLine,
            {
                "journal_entry_id": source_journal.id,
                "line_no": 1,
                "account_id": repair_expense_account.id,
                "debit": Decimal("1000.00"),
                "credit": Decimal("0.00"),
            },
        )
    )

    source_balance = JournalEntryLine(
        **_minimal_kwargs(
            JournalEntryLine,
            {
                "journal_entry_id": source_journal.id,
                "line_no": 2,
                "account_id": balancing_account.id,
                "debit": Decimal("0.00"),
                "credit": Decimal("1000.00"),
            },
        )
    )

    db.add_all([source_line, source_balance])
    await db.flush()

    nonexpense_journal = JournalEntry(
        **_minimal_kwargs(
            JournalEntry,
            {
                "company_id": company.id,
                "entry_date": DAY1,
                "description": f"Non-expense source {suffix}",
                "status": JournalEntryStatus.POSTED,
                "created_by": actor.id,
            },
        )
    )
    db.add(nonexpense_journal)
    await db.flush()

    nonexpense_line = JournalEntryLine(
        **_minimal_kwargs(
            JournalEntryLine,
            {
                "journal_entry_id": nonexpense_journal.id,
                "line_no": 1,
                "account_id": nonexpense_account.id,
                "debit": Decimal("500.00"),
                "credit": Decimal("0.00"),
            },
        )
    )

    nonexpense_balance = JournalEntryLine(
        **_minimal_kwargs(
            JournalEntryLine,
            {
                "journal_entry_id": nonexpense_journal.id,
                "line_no": 2,
                "account_id": balancing_account.id,
                "debit": Decimal("0.00"),
                "credit": Decimal("500.00"),
            },
        )
    )

    db.add_all([nonexpense_line, nonexpense_balance])
    await db.flush()

    future_journal = JournalEntry(
        **_minimal_kwargs(
            JournalEntry,
            {
                "company_id": company.id,
                "entry_date": DAY3,
                "description": f"Future expense source {suffix}",
                "status": JournalEntryStatus.POSTED,
                "created_by": actor.id,
            },
        )
    )
    db.add(future_journal)
    await db.flush()

    future_line = JournalEntryLine(
        **_minimal_kwargs(
            JournalEntryLine,
            {
                "journal_entry_id": future_journal.id,
                "line_no": 1,
                "account_id": repair_expense_account.id,
                "debit": Decimal("500.00"),
                "credit": Decimal("0.00"),
            },
        )
    )

    future_balance = JournalEntryLine(
        **_minimal_kwargs(
            JournalEntryLine,
            {
                "journal_entry_id": future_journal.id,
                "line_no": 2,
                "account_id": balancing_account.id,
                "debit": Decimal("0.00"),
                "credit": Decimal("500.00"),
            },
        )
    )

    db.add_all([future_line, future_balance])
    await db.flush()

    group = FixedAssetGroup(
        company_id=company.id,
        code=f"FARI-{suffix}",
        name=f"FARI group {suffix}",
    )
    db.add(group)
    await db.flush()

    asset = FixedAsset(
        **_minimal_kwargs(
            FixedAsset,
            {
                "company_id": company.id,
                "asset_group_id": group.id,
                "asset_number": f"FA-{suffix}",
                "name": f"FARI asset {suffix}",
                "status": FixedAssetStatus.IN_SERVICE,
                "acquisition_date": date(2026, 1, 1),
                "in_service_date": date(2026, 1, 15),
                "original_cost": Decimal("10000.00"),
                "salvage_value": Decimal("1000.00"),
                "useful_life_months": 60,
                "depreciation_method": "straight_line",
                "asset_account_id": asset_account.id,
                "accumulated_depreciation_account_id":
                    accumulated_account.id,
                "depreciation_expense_account_id":
                    depreciation_expense_account.id,
                "created_by": actor.id,
            },
        )
    )
    db.add(asset)
    await db.flush()

    await db.commit()

    return SimpleNamespace(
        company=company.id,
        other_company=other_company.id,
        actor=actor.id,
        asset=asset.id,
        source_line=source_line.id,
        source_journal=source_journal.id,
        nonexpense_line=nonexpense_line.id,
        future_line=future_line.id,
        asset_account=asset_account.id,
        expense_account=repair_expense_account.id,
    )


def payload(
    source_line_id,
    key,
    *,
    operation_type=FixedAssetRepairImprovementType.IMPROVEMENT,
    amount="100.00",
    operation_date=DAY2,
    description="capital improvement",
):
    return FixedAssetRepairImprovementCreate(
        operation_type=operation_type,
        operation_date=operation_date,
        amount=Decimal(amount),
        source_journal_entry_line_id=source_line_id,
        request_key=key,
        description=description,
    )


async def create(db, f, key, **overrides):
    return await create_fixed_asset_repair_improvement(
        db=db,
        company_id=f.company,
        fixed_asset_id=f.asset,
        data=payload(
            f.source_line,
            key,
            **overrides,
        ),
        created_by=f.actor,
    )


async def asset_cost(db, asset_id):
    asset = await db.get(FixedAsset, asset_id)
    return Decimal(asset.original_cost)


async def original_entries(db, operation_id):
    result = await db.execute(
        select(JournalEntry).where(
            JournalEntry.fixed_asset_repair_improvement_id
            == operation_id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )
    return result.scalars().all()


async def test_improvement_posts_canonical_reclassification_and_retry_is_idempotent(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        op = await create(
            db,
            f,
            "imp-1",
            amount="300.00",
        )

        assert op.operation_type == (
            FixedAssetRepairImprovementType.IMPROVEMENT
        )
        assert op.journal_entry_id is not None
        assert await asset_cost(db, f.asset) == Decimal("10300.00")

        entries = await original_entries(db, op.id)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.status == JournalEntryStatus.POSTED

        lines = (
            await db.scalars(
                select(JournalEntryLine)
                .where(
                    JournalEntryLine.journal_entry_id
                    == entry.id
                )
                .order_by(JournalEntryLine.line_no)
            )
        ).all()

        assert len(lines) == 2

        debit = next(
            line
            for line in lines
            if Decimal(line.debit) > 0
        )
        credit = next(
            line
            for line in lines
            if Decimal(line.credit) > 0
        )

        assert debit.account_id == f.asset_account
        assert Decimal(debit.debit) == Decimal("300.00")
        assert credit.account_id == f.expense_account
        assert Decimal(credit.credit) == Decimal("300.00")

        retry = await create(
            db,
            f,
            "imp-1",
            amount="300.00",
        )

        assert retry.id == op.id
        assert await asset_cost(db, f.asset) == Decimal("10300.00")
        assert len(await original_entries(db, op.id)) == 1


async def test_request_key_is_immutable(engine):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        await create(
            db,
            f,
            "immutable-1",
            amount="100.00",
        )

        with pytest.raises(
            FixedAssetRepairImprovementError,
            match="request[ _]key",
        ):
            await create(
                db,
                f,
                "immutable-1",
                amount="101.00",
            )


async def test_partial_allocation_and_repair_preserve_asset_cost(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        first = await create(
            db,
            f,
            "partial-1",
            amount="600.00",
        )
        assert first.id is not None
        assert await asset_cost(db, f.asset) == Decimal("10600.00")

        repair = await create(
            db,
            f,
            "partial-2",
            operation_type=FixedAssetRepairImprovementType.REPAIR,
            amount="300.00",
            description="ordinary repair",
        )

        assert repair.journal_entry_id is None
        assert await asset_cost(db, f.asset) == Decimal("10600.00")

        with pytest.raises(
            FixedAssetRepairImprovementError,
            match="exceed",
        ):
            await create(
                db,
                f,
                "partial-3",
                amount="101.00",
            )


async def test_source_must_be_expense_and_not_from_future(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        with pytest.raises(
            FixedAssetRepairImprovementError,
            match="expense",
        ):
            await create_fixed_asset_repair_improvement(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                data=payload(
                    f.nonexpense_line,
                    "bad-account",
                    amount="100.00",
                ),
                created_by=f.actor,
            )

        with pytest.raises(
            FixedAssetRepairImprovementError,
            match="date|preced",
        ):
            await create_fixed_asset_repair_improvement(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                data=payload(
                    f.future_line,
                    "future-source",
                    amount="100.00",
                    operation_date=DAY2,
                ),
                created_by=f.actor,
            )


async def test_company_boundary_and_asset_status_are_enforced(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        with pytest.raises(
            (FixedAssetRepairImprovementError, HTTPException)
        ) as exc_info:
            await create_fixed_asset_repair_improvement(
                db=db,
                company_id=f.other_company,
                fixed_asset_id=f.asset,
                data=payload(
                    f.source_line,
                    "wrong-company",
                    amount="100.00",
                ),
                created_by=f.actor,
            )

        if isinstance(exc_info.value, HTTPException):
            assert exc_info.value.status_code == 409

        wrong_company_rows = (
            await db.scalars(
                select(FixedAssetRepairImprovement).where(
                    FixedAssetRepairImprovement.company_id
                    == f.other_company
                )
            )
        ).all()

        assert wrong_company_rows == []

        asset = await db.get(FixedAsset, f.asset)
        asset.status = FixedAssetStatus.DISPOSED
        await db.flush()

        with pytest.raises(
            FixedAssetRepairImprovementError,
            match="(?i:status|service|disposed)",
        ):
            await create(
                db,
                f,
                "disposed-asset",
                amount="100.00",
            )


async def test_improvement_reversal_restores_cost_and_is_idempotent(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        op = await create(
            db,
            f,
            "reverse-source",
            amount="250.00",
        )

        assert await asset_cost(db, f.asset) == Decimal("10250.00")

        reversal = await reverse_fixed_asset_repair_improvement(
            db=db,
            company_id=f.company,
            fixed_asset_id=f.asset,
            operation_id=op.id,
            reversal_date=DAY3,
            request_key="reverse-1",
            reversed_by=f.actor,
        )

        assert reversal.reversal_of_id == op.id
        assert reversal.journal_entry_id is not None
        assert await asset_cost(db, f.asset) == Decimal("10000.00")

        retry = await reverse_fixed_asset_repair_improvement(
            db=db,
            company_id=f.company,
            fixed_asset_id=f.asset,
            operation_id=op.id,
            reversal_date=DAY3,
            request_key="reverse-1",
            reversed_by=f.actor,
        )

        assert retry.id == reversal.id
        assert await asset_cost(db, f.asset) == Decimal("10000.00")

        reversal_entries = (
            await db.scalars(
                select(JournalEntry).where(
                    JournalEntry.reversal_of_id
                    == op.journal_entry_id
                )
            )
        ).all()

        assert len(reversal_entries) == 1


async def test_reversal_date_cannot_precede_operation(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        op = await create(
            db,
            f,
            "reverse-date-source",
            amount="100.00",
        )

        with pytest.raises(
            FixedAssetRepairImprovementError,
            match="date|preced",
        ):
            await reverse_fixed_asset_repair_improvement(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                operation_id=op.id,
                reversal_date=DAY1,
                request_key="reverse-too-early",
                reversed_by=f.actor,
            )


async def test_closed_period_blocks_operation(engine):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        period = await db.scalar(
            select(AccountingPeriod).where(
                AccountingPeriod.company_id == f.company,
                AccountingPeriod.year == 2026,
                AccountingPeriod.month == 2,
            )
        )

        period.status = "closed"
        period.is_locked = True
        await db.flush()

        with pytest.raises(Exception):
            await create(
                db,
                f,
                "closed-period",
                amount="100.00",
            )
