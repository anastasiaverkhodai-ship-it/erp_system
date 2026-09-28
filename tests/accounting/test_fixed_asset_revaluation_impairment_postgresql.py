import os
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.models
from app.core.config import settings
from app.core.database import Base
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.company import Company
from app.models.fixed_asset import (
    FixedAsset,
    FixedAssetStatus,
)
from app.models.fixed_asset_revaluation_impairment import (
    FixedAssetRevaluationImpairment,
    FixedAssetRevaluationImpairmentType,
)
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import JournalEntryLine
from app.models.user import User
from app.models import FixedAssetGroup
from app.schemas.fixed_asset_revaluation_impairment import (
    FixedAssetRevaluationImpairmentCreate,
)
from app.services.accounting_posting import (
    AccountingPostingError,
    post_journal_entry,
)
from app.services.accounting_reversal import (
    AccountingReversalError,
    reverse_journal_entry,
)
from app.services.fixed_asset_depreciation_service import (
    _calculate_amount,
)
from app.services.fixed_asset_revaluation_impairment_service import (
    FixedAssetRevaluationImpairmentError,
    create_fixed_asset_revaluation_impairment,
    reverse_fixed_asset_revaluation_impairment,
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

    schema = "test_fari2_" + uuid.uuid4().hex

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
                "name": f"FARI2 company {suffix}",
            },
        )
    )
    db.add(company)
    await db.flush()

    other_company = Company(
        **_minimal_kwargs(
            Company,
            {
                "name": f"FARI2 other company {suffix}",
            },
        )
    )
    db.add(other_company)
    await db.flush()

    actor = User(
        **_minimal_kwargs(
            User,
            {
                "email": f"fari2-{suffix}@example.test",
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

    revaluation_account = Account(
        **_minimal_kwargs(
            Account,
            {
                "company_id": company.id,
                "code": f"42{suffix[:4]}",
                "name": f"Revaluation reserve {suffix}",
                "account_type": "equity",
                "normal_balance": "credit",
                "is_active": True,
                "is_postable": True,
            },
        )
    )

    impairment_account = Account(
        **_minimal_kwargs(
            Account,
            {
                "company_id": company.id,
                "code": f"97{suffix[:4]}",
                "name": f"Impairment expense {suffix}",
                "account_type": "expense",
                "normal_balance": "debit",
                "is_active": True,
                "is_postable": True,
            },
        )
    )

    foreign_counterpart = Account(
        **_minimal_kwargs(
            Account,
            {
                "company_id": other_company.id,
                "code": f"99{suffix[:4]}",
                "name": f"Foreign counterpart {suffix}",
                "account_type": "expense",
                "normal_balance": "debit",
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
            revaluation_account,
            impairment_account,
            foreign_counterpart,
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

    db.add(
        AccountingPeriod(
            **_minimal_kwargs(
                AccountingPeriod,
                {
                    "company_id": other_company.id,
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

    group = FixedAssetGroup(
        company_id=company.id,
        code=f"FARI2-{suffix}",
        name=f"FARI2 group {suffix}",
    )
    db.add(group)
    await db.flush()

    asset = FixedAsset(
        **_minimal_kwargs(
            FixedAsset,
            {
                "company_id": company.id,
                "asset_group_id": group.id,
                "asset_number": f"FA2-{suffix}",
                "name": f"FARI2 asset {suffix}",
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
        asset_account=asset_account.id,
        revaluation_account=revaluation_account.id,
        impairment_account=impairment_account.id,
        foreign_counterpart=foreign_counterpart.id,
    )


def payload(
    f,
    key,
    *,
    operation_type=FixedAssetRevaluationImpairmentType.REVALUATION,
    carrying="11000.00",
    operation_date=DAY2,
    counterpart=None,
    description="fixed asset valuation",
):
    return FixedAssetRevaluationImpairmentCreate(
        operation_type=operation_type,
        operation_date=operation_date,
        carrying_amount_after=Decimal(carrying),
        counterpart_account_id=(
            counterpart
            if counterpart is not None
            else f.revaluation_account
        ),
        request_key=key,
        description=description,
    )


async def create(db, f, key, **overrides):
    return await create_fixed_asset_revaluation_impairment(
        db=db,
        company_id=f.company,
        fixed_asset_id=f.asset,
        data=payload(
            f,
            key,
            **overrides,
        ),
        created_by=f.actor,
    )


async def asset_cost(db, asset_id):
    asset = await db.get(FixedAsset, asset_id)
    return Decimal(asset.original_cost)


async def journal_for(db, operation_id):
    return await db.scalar(
        select(JournalEntry).where(
            JournalEntry.fixed_asset_revaluation_impairment_id
            == operation_id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )


async def journal_lines(db, journal_id):
    return (
        await db.scalars(
            select(JournalEntryLine)
            .where(
                JournalEntryLine.journal_entry_id
                == journal_id
            )
            .order_by(JournalEntryLine.line_no)
        )
    ).all()


async def test_upward_revaluation_posts_canonical_journal_and_is_idempotent(
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
            "revaluation-up-1",
            carrying="11000.00",
        )

        assert op.operation_type == (
            FixedAssetRevaluationImpairmentType.REVALUATION
        )
        assert Decimal(op.carrying_amount_before) == Decimal(
            "10000.00"
        )
        assert Decimal(op.carrying_amount_after) == Decimal(
            "11000.00"
        )
        assert Decimal(op.amount) == Decimal("1000.00")
        assert Decimal(op.original_cost_before) == Decimal(
            "10000.00"
        )
        assert Decimal(op.original_cost_after) == Decimal(
            "11000.00"
        )
        assert await asset_cost(
            db,
            f.asset,
        ) == Decimal("11000.00")

        journal = await journal_for(db, op.id)

        assert journal is not None
        assert journal.id == op.journal_entry_id
        assert journal.status == JournalEntryStatus.POSTED

        lines = await journal_lines(db, journal.id)

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
        assert Decimal(debit.debit) == Decimal("1000.00")
        assert credit.account_id == f.revaluation_account
        assert Decimal(credit.credit) == Decimal("1000.00")

        retry = await create(
            db,
            f,
            "revaluation-up-1",
            carrying="11000.00",
        )

        assert retry.id == op.id
        assert await asset_cost(
            db,
            f.asset,
        ) == Decimal("11000.00")

        rows = (
            await db.scalars(
                select(
                    FixedAssetRevaluationImpairment
                ).where(
                    FixedAssetRevaluationImpairment.company_id
                    == f.company
                )
            )
        ).all()

        assert len(rows) == 1


async def test_impairment_posts_downward_journal_and_changes_basis(
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
            "impairment-1",
            operation_type=(
                FixedAssetRevaluationImpairmentType.IMPAIRMENT
            ),
            carrying="8000.00",
            counterpart=f.impairment_account,
            description="impairment loss",
        )

        assert Decimal(op.carrying_amount_before) == Decimal(
            "10000.00"
        )
        assert Decimal(op.carrying_amount_after) == Decimal(
            "8000.00"
        )
        assert Decimal(op.amount) == Decimal("2000.00")
        assert await asset_cost(
            db,
            f.asset,
        ) == Decimal("8000.00")

        journal = await journal_for(db, op.id)

        assert journal is not None
        assert journal.status == JournalEntryStatus.POSTED

        lines = await journal_lines(db, journal.id)

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

        assert debit.account_id == f.impairment_account
        assert Decimal(debit.debit) == Decimal("2000.00")
        assert credit.account_id == f.asset_account
        assert Decimal(credit.credit) == Decimal("2000.00")

        asset = await db.get(FixedAsset, f.asset)

        amount = _calculate_amount(
            asset=asset,
            accumulated_before=Decimal("0.00"),
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
        )

        assert amount == Decimal("116.67")


async def test_revaluation_changes_future_depreciation_basis_without_history_rewrite(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        asset = await db.get(FixedAsset, f.asset)

        before = _calculate_amount(
            asset=asset,
            accumulated_before=Decimal("0.00"),
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
        )

        assert before == Decimal("150.00")

        op = await create(
            db,
            f,
            "basis-up",
            carrying="11000.00",
        )

        assert Decimal(op.accumulated_depreciation) == Decimal(
            "0.00"
        )

        asset = await db.get(FixedAsset, f.asset)

        after = _calculate_amount(
            asset=asset,
            accumulated_before=Decimal("0.00"),
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
        )

        assert after == Decimal("166.67")

        count = await db.scalar(
            text(
                "SELECT count(*) "
                "FROM fixed_asset_depreciations "
                "WHERE fixed_asset_id = :asset_id"
            ),
            {"asset_id": f.asset},
        )

        assert count == 0


@pytest.mark.parametrize(
    (
        "operation_type",
        "carrying",
        "counterpart_attr",
        "key",
    ),
    [
        (
            FixedAssetRevaluationImpairmentType.REVALUATION,
            "11250.00",
            "revaluation_account",
            "reverse-up-source",
        ),
        (
            FixedAssetRevaluationImpairmentType.IMPAIRMENT,
            "8250.00",
            "impairment_account",
            "reverse-down-source",
        ),
    ],
)
async def test_reversal_restores_cost_and_creates_canonical_reversal(
    engine,
    operation_type,
    carrying,
    counterpart_attr,
    key,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        op = await create(
            db,
            f,
            key,
            operation_type=operation_type,
            carrying=carrying,
            counterpart=getattr(
                f,
                counterpart_attr,
            ),
        )

        original_journal = await journal_for(
            db,
            op.id,
        )

        assert original_journal is not None

        reversal = (
            await reverse_fixed_asset_revaluation_impairment(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                operation_id=op.id,
                reversal_date=DAY3,
                request_key=f"reversal-{key}",
                reversed_by=f.actor,
            )
        )

        assert reversal.reversal_of_id == op.id
        assert reversal.journal_entry_id is not None

        assert await asset_cost(
            db,
            f.asset,
        ) == Decimal("10000.00")

        reversal_journal = await db.get(
            JournalEntry,
            reversal.journal_entry_id,
        )

        assert reversal_journal is not None
        assert reversal_journal.status == JournalEntryStatus.POSTED
        assert reversal_journal.reversal_of_id == (
            original_journal.id
        )
        assert (
            reversal_journal.fixed_asset_revaluation_impairment_id
            == reversal.id
        )

        retry = (
            await reverse_fixed_asset_revaluation_impairment(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                operation_id=op.id,
                reversal_date=DAY3,
                request_key=f"reversal-{key}",
                reversed_by=f.actor,
            )
        )

        assert retry.id == reversal.id

        with pytest.raises(
            FixedAssetRevaluationImpairmentError,
            match="cannot reverse a reversal",
        ):
            await reverse_fixed_asset_revaluation_impairment(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                operation_id=reversal.id,
                reversal_date=DAY3,
                request_key=f"double-{key}",
                reversed_by=f.actor,
            )


async def test_request_key_payload_is_immutable(engine):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        await create(
            db,
            f,
            "immutable-key",
            carrying="11000.00",
        )

        with pytest.raises(
            FixedAssetRevaluationImpairmentError,
            match="request key",
        ):
            await create(
                db,
                f,
                "immutable-key",
                carrying="12000.00",
            )


async def test_company_counterpart_and_disposed_guards(engine):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        with pytest.raises(
            FixedAssetRevaluationImpairmentError,
            match="fixed asset not found",
        ):
            await create_fixed_asset_revaluation_impairment(
                db=db,
                company_id=f.other_company,
                fixed_asset_id=f.asset,
                data=payload(
                    f,
                    "wrong-company",
                ),
                created_by=f.actor,
            )

        with pytest.raises(
            FixedAssetRevaluationImpairmentError,
            match="counterpart account not found",
        ):
            await create(
                db,
                f,
                "foreign-counterpart",
                counterpart=f.foreign_counterpart,
            )

        asset = await db.get(
            FixedAsset,
            f.asset,
        )

        asset.status = FixedAssetStatus.DISPOSED
        await db.flush()

        with pytest.raises(
            FixedAssetRevaluationImpairmentError,
            match="Disposed",
        ):
            await create(
                db,
                f,
                "disposed-asset",
            )


async def test_closed_period_blocks_operation(engine):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        period = await db.scalar(
            select(AccountingPeriod).where(
                AccountingPeriod.company_id
                == f.company,
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
            )


async def test_generic_posting_and_reversal_bypass_are_blocked(
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
            "bypass-source",
            carrying="11000.00",
        )

        journal = await journal_for(
            db,
            op.id,
        )

        assert journal is not None

        with pytest.raises(
            AccountingReversalError,
            match="revaluation/impairment lifecycle",
        ):
            await reverse_journal_entry(
                db=db,
                company_id=f.company,
                journal_entry_id=journal.id,
                reversal_date=DAY3,
                reversed_by=f.actor,
            )

        manual = JournalEntry(
            **_minimal_kwargs(
                JournalEntry,
                {
                    "company_id": f.company,
                    "fixed_asset_revaluation_impairment_id":
                        op.id,
                    "entry_date": DAY3,
                    "description": "forbidden generic valuation",
                    "status": JournalEntryStatus.DRAFT,
                    "created_by": f.actor,
                },
            )
        )

        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(manual)
                await db.flush()

        assert manual.id is None

        with pytest.raises(
            AccountingReversalError,
            match="revaluation/impairment lifecycle",
        ):
            await reverse_journal_entry(
                db=db,
                company_id=f.company,
                journal_entry_id=journal.id,
                reversal_date=DAY3,
                reversed_by=f.actor,
            )
