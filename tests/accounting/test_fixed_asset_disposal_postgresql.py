import os
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
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
from app.models.fixed_asset_disposal import FixedAssetDisposal
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.models.user import User
from app.models import FixedAssetGroup
from app.schemas.fixed_asset_disposal import FixedAssetDisposalCreate
from app.services.accounting_posting import (
    AccountingPostingError,
    post_journal_entry,
)
from app.services.accounting_reversal import (
    AccountingReversalError,
    reverse_journal_entry,
)
from app.services.fixed_asset_depreciation_service import (
    FixedAssetDepreciationError,
    create_and_post_depreciation,
)
from app.services.fixed_asset_disposal_service import (
    FixedAssetDisposalError,
    create_fixed_asset_disposal,
    reverse_fixed_asset_disposal,
)
from app.services.fixed_asset_revaluation_impairment_service import (
    FixedAssetRevaluationImpairmentError,
    create_fixed_asset_revaluation_impairment,
)
from app.schemas.fixed_asset_revaluation_impairment import (
    FixedAssetRevaluationImpairmentCreate,
)


def _minimal_kwargs(model, explicit):
    result = dict(explicit)

    for column in model.__table__.columns:
        if column.name in result:
            continue
        if column.primary_key:
            continue
        if column.nullable:
            continue
        if column.default is not None:
            continue
        if column.server_default is not None:
            continue

        pytype = None

        try:
            pytype = column.type.python_type
        except Exception:
            pass

        if pytype is str:
            result[column.name] = f"test-{column.name}"
        elif pytype is bool:
            result[column.name] = False
        elif pytype is int:
            result[column.name] = 1
        elif pytype is Decimal:
            result[column.name] = Decimal("0.00")
        elif pytype is date:
            result[column.name] = date(2026, 2, 1)

    return result


@pytest_asyncio.fixture
async def engine():
    url = os.environ.get(
        "DATABASE_URL",
        str(settings.database_url),
    )

    if url.startswith("postgresql://"):
        url = url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )

    engine = create_async_engine(
        url,
        poolclass=NullPool,
    )

    async with engine.connect() as connection:
        dialect = connection.dialect.name

    if dialect != "postgresql":
        await engine.dispose()
        pytest.skip(
            "real PostgreSQL required"
        )

    try:
        yield engine
    finally:
        await engine.dispose()


async def seed(db):
    suffix = uuid.uuid4().hex[:10]

    company = Company(
        **_minimal_kwargs(
            Company,
            {
                "name": f"FAD company {suffix}",
            },
        )
    )
    db.add(company)
    await db.flush()

    other_company = Company(
        **_minimal_kwargs(
            Company,
            {
                "name": f"FAD other company {suffix}",
            },
        )
    )
    db.add(other_company)
    await db.flush()

    actor = User(
        **_minimal_kwargs(
            User,
            {
                "email": f"fad-{suffix}@example.test",
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

    disposal_account = Account(
        **_minimal_kwargs(
            Account,
            {
                "company_id": company.id,
                "code": f"97{suffix[:4]}",
                "name": f"Disposal expense {suffix}",
                "account_type": "expense",
                "normal_balance": "debit",
                "is_active": True,
                "is_postable": True,
            },
        )
    )

    counterpart_account = Account(
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

    foreign_disposal_account = Account(
        **_minimal_kwargs(
            Account,
            {
                "company_id": other_company.id,
                "code": f"99{suffix[:4]}",
                "name": f"Foreign disposal {suffix}",
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
            disposal_account,
            counterpart_account,
            foreign_disposal_account,
        ]
    )
    await db.flush()

    for target_company in (
        company,
        other_company,
    ):
        db.add(
            AccountingPeriod(
                **_minimal_kwargs(
                    AccountingPeriod,
                    {
                        "company_id": target_company.id,
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
        code=f"FAD-{suffix}",
        name=f"FAD group {suffix}",
    )
    db.add(group)
    await db.flush()

    asset = FixedAsset(
        **_minimal_kwargs(
            FixedAsset,
            {
                "company_id": company.id,
                "asset_group_id": group.id,
                "asset_number": f"FAD-{suffix}",
                "name": f"Disposal asset {suffix}",
                "status": FixedAssetStatus.IN_SERVICE,
                "acquisition_date": date(2026, 1, 1),
                "in_service_date": date(2026, 1, 15),
                "original_cost": Decimal("10000.00"),
                "salvage_value": Decimal("1000.00"),
                "useful_life_months": 60,
                "depreciation_method": "straight_line",
                "asset_account_id": asset_account.id,
                "accumulated_depreciation_account_id": (
                    accumulated_account.id
                ),
                "depreciation_expense_account_id": (
                    depreciation_expense_account.id
                ),
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
        accumulated_account=accumulated_account.id,
        depreciation_expense_account=(
            depreciation_expense_account.id
        ),
        disposal_account=disposal_account.id,
        counterpart_account=counterpart_account.id,
        foreign_disposal_account=(
            foreign_disposal_account.id
        ),
    )


def payload(f, key):
    return FixedAssetDisposalCreate(
        disposal_date=date(2026, 2, 20),
        disposal_account_id=f.disposal_account,
        request_key=key,
        description="Fixed asset disposal test",
    )


async def create(db, f, key):
    return await create_fixed_asset_disposal(
        db=db,
        company_id=f.company,
        fixed_asset_id=f.asset,
        data=payload(f, key),
        created_by=f.actor,
    )


async def journal_for(db, operation_id):
    return await db.scalar(
        select(JournalEntry).where(
            JournalEntry.fixed_asset_disposal_id
            == operation_id
        )
    )


async def journal_lines(db, journal_id):
    result = await db.scalars(
        select(JournalEntryLine)
        .where(
            JournalEntryLine.journal_entry_id
            == journal_id
        )
        .order_by(
            JournalEntryLine.line_no
        )
    )

    return list(result.all())


@pytest.mark.asyncio
async def test_disposal_posts_canonical_journal_and_is_idempotent(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        row = await create(
            db,
            f,
            "dispose-happy",
        )

        retry = await create(
            db,
            f,
            "dispose-happy",
        )

        assert retry.id == row.id
        assert row.original_cost == Decimal(
            "10000.00"
        )
        assert row.accumulated_depreciation == Decimal(
            "0.00"
        )
        assert row.carrying_amount == Decimal(
            "10000.00"
        )
        assert row.salvage_value == Decimal(
            "1000.00"
        )
        assert row.previous_status == (
            FixedAssetStatus.IN_SERVICE.value
        )

        asset = await db.get(
            FixedAsset,
            f.asset,
        )

        assert (
            asset.status
            == FixedAssetStatus.DISPOSED
        )

        journal = await journal_for(
            db,
            row.id,
        )

        assert journal is not None
        assert (
            journal.id
            == row.journal_entry_id
        )
        assert (
            journal.status
            == JournalEntryStatus.POSTED
        )
        assert (
            journal.fixed_asset_disposal_id
            == row.id
        )

        lines = await journal_lines(
            db,
            journal.id,
        )

        assert len(lines) == 2

        by_account = {
            line.account_id: line
            for line in lines
        }

        assert (
            by_account[
                f.disposal_account
            ].debit
            == Decimal("10000.00")
        )
        assert (
            by_account[
                f.disposal_account
            ].credit
            == Decimal("0.00")
        )

        assert (
            by_account[
                f.asset_account
            ].debit
            == Decimal("0.00")
        )
        assert (
            by_account[
                f.asset_account
            ].credit
            == Decimal("10000.00")
        )


@pytest.mark.asyncio
async def test_request_key_payload_is_immutable(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        await create(
            db,
            f,
            "immutable",
        )

        changed = FixedAssetDisposalCreate(
            disposal_date=date(2026, 2, 21),
            disposal_account_id=(
                f.disposal_account
            ),
            request_key="immutable",
            description=(
                "different payload"
            ),
        )

        with pytest.raises(
            FixedAssetDisposalError,
            match="request key already used",
        ):
            await create_fixed_asset_disposal(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                data=changed,
                created_by=f.actor,
            )


@pytest.mark.asyncio
async def test_company_account_and_repeat_disposal_guards(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        with pytest.raises(
            FixedAssetDisposalError,
            match="fixed asset not found",
        ):
            await create_fixed_asset_disposal(
                db=db,
                company_id=f.other_company,
                fixed_asset_id=f.asset,
                data=payload(
                    f,
                    "wrong-company",
                ),
                created_by=f.actor,
            )

        foreign = FixedAssetDisposalCreate(
            disposal_date=date(2026, 2, 20),
            disposal_account_id=(
                f.foreign_disposal_account
            ),
            request_key="foreign-account",
            description=None,
        )

        with pytest.raises(
            FixedAssetDisposalError,
            match="disposal account not found",
        ):
            await create_fixed_asset_disposal(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                data=foreign,
                created_by=f.actor,
            )

        await create(
            db,
            f,
            "first-disposal",
        )

        with pytest.raises(
            FixedAssetDisposalError,
            match="already disposed",
        ):
            await create(
                db,
                f,
                "second-disposal",
            )


@pytest.mark.asyncio
async def test_closed_period_blocks_disposal(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        period = await db.scalar(
            select(AccountingPeriod).where(
                AccountingPeriod.company_id
                == f.company,
                AccountingPeriod.year
                == 2026,
                AccountingPeriod.month
                == 2,
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


@pytest.mark.asyncio
async def test_disposal_reversal_restores_status_and_canonical_journal(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        original = await create(
            db,
            f,
            "reverse-original",
        )

        reversal = (
            await reverse_fixed_asset_disposal(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                operation_id=original.id,
                reversal_date=date(
                    2026,
                    2,
                    21,
                ),
                request_key=(
                    "reverse-disposal"
                ),
                reversed_by=f.actor,
            )
        )

        retry = (
            await reverse_fixed_asset_disposal(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                operation_id=original.id,
                reversal_date=date(
                    2026,
                    2,
                    21,
                ),
                request_key=(
                    "reverse-disposal"
                ),
                reversed_by=f.actor,
            )
        )

        assert retry.id == reversal.id
        assert (
            reversal.reversal_of_id
            == original.id
        )

        asset = await db.get(
            FixedAsset,
            f.asset,
        )

        assert (
            asset.status
            == FixedAssetStatus.IN_SERVICE
        )

        original_journal = await db.get(
            JournalEntry,
            original.journal_entry_id,
        )

        reversal_journal = await db.get(
            JournalEntry,
            reversal.journal_entry_id,
        )

        assert (
            original_journal.status
            == JournalEntryStatus.REVERSED
        )

        assert (
            reversal_journal.reversal_of_id
            == original_journal.id
        )

        assert (
            reversal_journal
            .fixed_asset_disposal_id
            == reversal.id
        )

        original_lines = (
            await journal_lines(
                db,
                original_journal.id,
            )
        )

        reversal_lines = (
            await journal_lines(
                db,
                reversal_journal.id,
            )
        )

        assert len(original_lines) == len(
            reversal_lines
        )

        for original_line, reversal_line in zip(
            original_lines,
            reversal_lines,
        ):
            assert (
                original_line.account_id
                == reversal_line.account_id
            )
            assert (
                original_line.debit
                == reversal_line.credit
            )
            assert (
                original_line.credit
                == reversal_line.debit
            )


@pytest.mark.asyncio
async def test_suspended_asset_reversal_restores_suspended(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        asset = await db.get(
            FixedAsset,
            f.asset,
        )

        asset.status = (
            FixedAssetStatus.SUSPENDED
        )
        await db.flush()

        original = await create(
            db,
            f,
            "suspended-disposal",
        )

        assert (
            original.previous_status
            == FixedAssetStatus.SUSPENDED.value
        )

        await reverse_fixed_asset_disposal(
            db=db,
            company_id=f.company,
            fixed_asset_id=f.asset,
            operation_id=original.id,
            reversal_date=date(
                2026,
                2,
                21,
            ),
            request_key=(
                "suspended-reversal"
            ),
            reversed_by=f.actor,
        )

        asset = await db.get(
            FixedAsset,
            f.asset,
        )

        assert (
            asset.status
            == FixedAssetStatus.SUSPENDED
        )


@pytest.mark.asyncio
async def test_generic_posting_and_reversal_bypass_are_blocked(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        row = await create(
            db,
            f,
            "generic-guard",
        )

        journal = await db.get(
            JournalEntry,
            row.journal_entry_id,
        )

        journal.status = (
            JournalEntryStatus.DRAFT
        )
        await db.flush()

        with pytest.raises(
            AccountingPostingError,
            match=(
                "fixed asset disposal "
                "lifecycle"
            ),
        ):
            await post_journal_entry(
                db,
                f.company,
                journal.id,
            )

        journal.status = (
            JournalEntryStatus.POSTED
        )
        await db.flush()

        with pytest.raises(
            AccountingReversalError,
            match=(
                "fixed asset disposal "
                "lifecycle"
            ),
        ):
            await reverse_journal_entry(
                db=db,
                company_id=f.company,
                journal_entry_id=journal.id,
                reversal_date=date(
                    2026,
                    2,
                    21,
                ),
                reversed_by=f.actor,
            )


@pytest.mark.asyncio
async def test_disposed_asset_blocks_future_depreciation_and_revaluation(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        await create(
            db,
            f,
            "downstream-guards",
        )

        with pytest.raises(
            FixedAssetDepreciationError,
            match="must be in service",
        ):
            await create_and_post_depreciation(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                request_key=(
                    "disposed-depreciation"
                ),
                period_start=date(
                    2026,
                    2,
                    1,
                ),
                period_end=date(
                    2026,
                    2,
                    28,
                ),
                posting_date=date(
                    2026,
                    2,
                    28,
                ),
                created_by=f.actor,
            )

        data = (
            FixedAssetRevaluationImpairmentCreate(
                operation_date=date(
                    2026,
                    2,
                    22,
                ),
                operation_type=(
                    "revaluation"
                ),
                carrying_amount_after=(
                    Decimal("11000.00")
                ),
                counterpart_account_id=(
                    f.counterpart_account
                ),
                request_key=(
                    "disposed-revaluation"
                ),
                description=None,
            )
        )

        with pytest.raises(
            FixedAssetRevaluationImpairmentError,
            match="Disposed",
        ):
            await create_fixed_asset_revaluation_impairment(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                data=data,
                created_by=f.actor,
            )


@pytest.mark.asyncio
async def test_disposal_with_accumulated_depreciation_uses_three_line_entry(
    engine,
):
    async with AsyncSession(
        engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        depreciation = (
            await create_and_post_depreciation(
                db=db,
                company_id=f.company,
                fixed_asset_id=f.asset,
                request_key="pre-disposal-dep",
                period_start=date(
                    2026,
                    2,
                    1,
                ),
                period_end=date(
                    2026,
                    2,
                    18,
                ),
                posting_date=date(
                    2026,
                    2,
                    18,
                ),
                created_by=f.actor,
            )
        )

        assert depreciation is not None

        row = await create(
            db,
            f,
            "with-depreciation",
        )

        assert (
            row.accumulated_depreciation
            > Decimal("0.00")
        )

        assert (
            row.carrying_amount
            == row.original_cost
            - row.accumulated_depreciation
        )

        journal = await journal_for(
            db,
            row.id,
        )

        lines = await journal_lines(
            db,
            journal.id,
        )

        assert len(lines) == 3

        by_account = {
            line.account_id: line
            for line in lines
        }

        assert (
            by_account[
                f.accumulated_account
            ].debit
            == row.accumulated_depreciation
        )

        assert (
            by_account[
                f.disposal_account
            ].debit
            == row.carrying_amount
        )

        assert (
            by_account[
                f.asset_account
            ].credit
            == row.original_cost
        )

        total_debit = sum(
            (
                Decimal(
                    line.debit or 0
                )
                for line in lines
            ),
            Decimal("0.00"),
        )

        total_credit = sum(
            (
                Decimal(
                    line.credit or 0
                )
                for line in lines
            ),
            Decimal("0.00"),
        )

        assert total_debit == total_credit
