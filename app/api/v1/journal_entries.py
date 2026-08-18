from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.account import Account
from app.models.company import Company
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import JournalEntryLine
from app.schemas.journal_entry import (
    JournalEntryCreate,
    JournalEntryResponse,
    JournalEntryReverseRequest,
    JournalEntryUpdate,
)
from app.services.accounting_posting import (
    AccountingPostingError,
    JournalEntryNotFoundError,
    post_journal_entry,
)

from app.services.accounting_reversal import (
    AccountingReversalError,
    JournalEntryReversalNotFoundError,
    reverse_journal_entry,
)

router = APIRouter(
    prefix="/companies/{company_id}/journal-entries",
    tags=["Journal Entries"],
)


# ---------------------------------------------------------
# GET JOURNAL ENTRY LIST
# ---------------------------------------------------------


@router.get(
    "",
    response_model=list[JournalEntryResponse],
)
async def get_journal_entries(
    company_id: int,
    _=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(JournalEntry)
        .options(
            selectinload(JournalEntry.lines)
        )
        .where(
            JournalEntry.company_id == company_id
        )
        .order_by(
            JournalEntry.entry_date.desc(),
            JournalEntry.id.desc(),
        )
    )

    return result.scalars().all()


# ---------------------------------------------------------
# GET ONE JOURNAL ENTRY
# ---------------------------------------------------------


@router.get(
    "/{journal_entry_id}",
    response_model=JournalEntryResponse,
)
async def get_journal_entry(
    company_id: int,
    journal_entry_id: int,
    _=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(JournalEntry)
        .options(
            selectinload(JournalEntry.lines)
        )
        .where(
            JournalEntry.id == journal_entry_id,
            JournalEntry.company_id == company_id,
        )
    )

    journal_entry = result.scalar_one_or_none()

    if journal_entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Journal entry not found",
        )

    return journal_entry


# ---------------------------------------------------------
# CREATE DRAFT JOURNAL ENTRY
# ---------------------------------------------------------


@router.post(
    "",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_journal_entry(
    company_id: int,
    data: JournalEntryCreate,
    _=Depends(
        require_company_permission(
            "journal_entries.create"
        )
    ),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        # Company must exist and be active.
        company_result = await db.execute(
            select(Company).where(
                Company.id == company_id,
                Company.is_active.is_(True),
            )
        )

        company = company_result.scalar_one_or_none()

        if company is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found",
            )

        # All accounts must belong to this company
        # and be active.
        account_ids = {
            line.account_id
            for line in data.lines
        }

        accounts_result = await db.execute(
            select(Account.id).where(
                Account.id.in_(account_ids),
                Account.company_id == company_id,
                Account.is_active.is_(True),
            )
        )

        valid_account_ids = set(
            accounts_result.scalars().all()
        )

        invalid_account_ids = (
            account_ids - valid_account_ids
        )

        if invalid_account_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Journal entry contains invalid, "
                    "inactive, or foreign-company accounts: "
                    f"{sorted(invalid_account_ids)}"
                ),
            )

        journal_entry = JournalEntry(
            company_id=company_id,
            document_id=None,
            entry_date=data.entry_date,
            description=data.description,
            status=JournalEntryStatus.DRAFT,
            created_by=current_user.id,
        )

        journal_entry.lines = [
            JournalEntryLine(
                line_no=line_no,
                account_id=line.account_id,
                debit=line.debit,
                credit=line.credit,
                description=line.description,
            )
            for line_no, line in enumerate(
                data.lines,
                start=1,
            )
        ]

        db.add(journal_entry)

        await db.commit()

        result = await db.execute(
            select(JournalEntry)
            .options(
                selectinload(JournalEntry.lines)
            )
            .where(
                JournalEntry.id == journal_entry.id
            )
        )

        return result.scalar_one()

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Journal entry could not be created "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise

    # ---------------------------------------------------------
# UPDATE DRAFT JOURNAL ENTRY
# ---------------------------------------------------------


@router.patch(
    "/{journal_entry_id}",
    response_model=JournalEntryResponse,
)
async def update_journal_entry(
    company_id: int,
    journal_entry_id: int,
    data: JournalEntryUpdate,
    _=Depends(
        require_company_permission(
            "journal_entries.update"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(JournalEntry)
            .where(
                JournalEntry.id == journal_entry_id,
                JournalEntry.company_id == company_id,
            )
            .with_for_update()
        )

        journal_entry = result.scalar_one_or_none()

        if journal_entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Journal entry not found",
            )

        if (
            journal_entry.status
            != JournalEntryStatus.DRAFT
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Only draft journal entries "
                    "can be updated"
                ),
            )

        update_data = data.model_dump(
            exclude_unset=True,
            exclude={"lines"},
        )

        for field, value in update_data.items():
            setattr(
                journal_entry,
                field,
                value,
            )

        if data.lines is not None:
            account_ids = {
                line.account_id
                for line in data.lines
            }

            accounts_result = await db.execute(
                select(Account.id).where(
                    Account.id.in_(account_ids),
                    Account.company_id == company_id,
                    Account.is_active.is_(True),
                )
            )

            valid_account_ids = set(
                accounts_result.scalars().all()
            )

            invalid_account_ids = (
                account_ids - valid_account_ids
            )

            if invalid_account_ids:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Journal entry contains invalid, "
                        "inactive, or foreign-company "
                        "accounts: "
                        f"{sorted(invalid_account_ids)}"
                    ),
                )

            await db.execute(
                delete(JournalEntryLine).where(
                    JournalEntryLine.journal_entry_id
                    == journal_entry.id
                )
            )

            for line_no, line in enumerate(
                data.lines,
                start=1,
            ):
                db.add(
                    JournalEntryLine(
                        journal_entry_id=journal_entry.id,
                        line_no=line_no,
                        account_id=line.account_id,
                        debit=line.debit,
                        credit=line.credit,
                        description=line.description,
                    )
                )

        await db.commit()

        result = await db.execute(
            select(JournalEntry)
            .options(
                selectinload(JournalEntry.lines)
            )
            .where(
                JournalEntry.id == journal_entry_id,
                JournalEntry.company_id == company_id,
            )
        )

        return result.scalar_one()

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Journal entry could not be updated "
                "because of a data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------
# DELETE DRAFT JOURNAL ENTRY
# ---------------------------------------------------------


@router.delete(
    "/{journal_entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_journal_entry(
    company_id: int,
    journal_entry_id: int,
    _=Depends(
        require_company_permission(
            "journal_entries.delete"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(JournalEntry)
            .where(
                JournalEntry.id == journal_entry_id,
                JournalEntry.company_id == company_id,
            )
            .with_for_update()
        )

        journal_entry = result.scalar_one_or_none()

        if journal_entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Journal entry not found",
            )

        if (
            journal_entry.status
            != JournalEntryStatus.DRAFT
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Only draft journal entries "
                    "can be deleted"
                ),
            )

        await db.delete(journal_entry)
        await db.commit()

        return None

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise

    # ---------------------------------------------------------
# POST JOURNAL ENTRY
# ---------------------------------------------------------


@router.post(
    "/{journal_entry_id}/post",
    response_model=JournalEntryResponse,
)
async def post_journal_entry_endpoint(
    company_id: int,
    journal_entry_id: int,
    _=Depends(
        require_company_permission(
            "journal_entries.approve"
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        await post_journal_entry(
            db=db,
            company_id=company_id,
            journal_entry_id=journal_entry_id,
        )

        await db.commit()

        result = await db.execute(
            select(JournalEntry)
            .options(
                selectinload(JournalEntry.lines)
            )
            .where(
                JournalEntry.id == journal_entry_id,
                JournalEntry.company_id == company_id,
            )
        )

        return result.scalar_one()

    except JournalEntryNotFoundError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except AccountingPostingError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------
# REVERSE JOURNAL ENTRY
# ---------------------------------------------------------


@router.post(
    "/{journal_entry_id}/reverse",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reverse_journal_entry_endpoint(
    company_id: int,
    journal_entry_id: int,
    data: JournalEntryReverseRequest,
    _=Depends(
        require_company_permission(
            "journal_entries.reverse"
        )
    ),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        reversal = await reverse_journal_entry(
            db=db,
            company_id=company_id,
            journal_entry_id=journal_entry_id,
            reversal_date=data.reversal_date,
            reversed_by=current_user.id,
        )

        reversal_id = reversal.id

        await db.commit()

        result = await db.execute(
            select(JournalEntry)
            .options(
                selectinload(JournalEntry.lines)
            )
            .where(
                JournalEntry.id == reversal_id,
                JournalEntry.company_id == company_id,
            )
        )

        return result.scalar_one()

    except JournalEntryReversalNotFoundError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except AccountingReversalError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Journal entry reversal could not be "
                "created because of a data conflict"
            ),
        ) from exc

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise