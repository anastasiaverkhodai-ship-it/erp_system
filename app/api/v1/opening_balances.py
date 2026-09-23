from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from app.schemas.opening_balance import (
    OpeningBalanceCreate,
    OpeningBalanceLineResponse,
    OpeningBalanceResponse,
    OpeningBalanceReverseRequest,
)
from app.services.accounting_posting import (
    AccountingPostingError,
    JournalEntryNotFoundError,
)
from app.services.accounting_reversal import (
    AccountingReversalError,
    JournalEntryReversalNotFoundError,
)
from app.services.opening_balance_service import (
    OpeningBalanceError,
    OpeningBalanceNotFoundError,
    create_opening_balance,
    load_opening_balance_response_data,
    post_opening_balance,
    reverse_opening_balance,
)


router = APIRouter(
    prefix="/companies/{company_id}/opening-balances",
    tags=["Opening Balances"],
)


async def _response(
    db: AsyncSession,
    company_id: int,
    opening_balance_id: int,
) -> OpeningBalanceResponse:
    opening, journal, reversal_id = (
        await load_opening_balance_response_data(
            db=db,
            company_id=company_id,
            opening_balance_id=opening_balance_id,
        )
    )

    return OpeningBalanceResponse(
        id=opening.id,
        company_id=opening.company_id,
        opening_date=opening.opening_date,
        description=opening.description,
        journal_entry_id=opening.journal_entry_id,
        journal_status=journal.status,
        created_by=opening.created_by,
        created_at=opening.created_at,
        posted_at=journal.posted_at,
        reversed_at=journal.reversed_at,
        reversal_journal_entry_id=reversal_id,
        lines=[
            OpeningBalanceLineResponse.model_validate(line)
            for line in journal.lines
        ],
    )


@router.post(
    "",
    response_model=OpeningBalanceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_opening_balance_endpoint(
    company_id: int,
    data: OpeningBalanceCreate,
    current_user: User = Depends(
        require_company_permission("journal_entries.create")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        opening = await create_opening_balance(
            db=db,
            company_id=company_id,
            created_by=current_user.id,
            data=data,
        )

        response = await _response(
            db=db,
            company_id=company_id,
            opening_balance_id=opening.id,
        )
        await db.commit()
        return response

    except OpeningBalanceNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except (OpeningBalanceError, IntegrityError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Opening balance conflict" if isinstance(exc, IntegrityError) else str(exc),
        ) from exc

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise


@router.get(
    "/{opening_balance_id}",
    response_model=OpeningBalanceResponse,
)
async def get_opening_balance_endpoint(
    company_id: int,
    opening_balance_id: int,
    current_user: User = Depends(
        require_company_permission("journal_entries.read")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await _response(
            db=db,
            company_id=company_id,
            opening_balance_id=opening_balance_id,
        )

    except OpeningBalanceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/{opening_balance_id}/post",
    response_model=OpeningBalanceResponse,
)
async def post_opening_balance_endpoint(
    company_id: int,
    opening_balance_id: int,
    current_user: User = Depends(
        require_company_permission("journal_entries.post")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        await post_opening_balance(
            db=db,
            company_id=company_id,
            opening_balance_id=opening_balance_id,
        )

        response = await _response(
            db=db,
            company_id=company_id,
            opening_balance_id=opening_balance_id,
        )
        await db.commit()
        return response

    except OpeningBalanceNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except (
        OpeningBalanceError,
        AccountingPostingError,
        IntegrityError,
        JournalEntryNotFoundError,
    ) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Opening balance conflict" if isinstance(exc, IntegrityError) else str(exc),
        ) from exc

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise


@router.post(
    "/{opening_balance_id}/reverse",
    response_model=OpeningBalanceResponse,
)
async def reverse_opening_balance_endpoint(
    company_id: int,
    opening_balance_id: int,
    data: OpeningBalanceReverseRequest,
    current_user: User = Depends(
        require_company_permission("journal_entries.reverse")
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        await reverse_opening_balance(
            db=db,
            company_id=company_id,
            opening_balance_id=opening_balance_id,
            reversal_date=data.reversal_date,
            reversed_by=current_user.id,
        )

        response = await _response(
            db=db,
            company_id=company_id,
            opening_balance_id=opening_balance_id,
        )
        await db.commit()
        return response

    except OpeningBalanceNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except (
        OpeningBalanceError,
        AccountingReversalError,
        IntegrityError,
        JournalEntryReversalNotFoundError,
    ) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Opening balance conflict" if isinstance(exc, IntegrityError) else str(exc),
        ) from exc

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise
