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

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.opening_balance_detail import OpeningBalanceDetail
    from app.models.document import Document
    from app.models.counterparty_open_item import CounterpartyOpenItem
    package=await db.scalar(select(OpeningBalanceDetail).where(
        OpeningBalanceDetail.company_id==company_id,OpeningBalanceDetail.opening_balance_id==opening.id))
    detail=None
    if package:
        document=await db.scalar(select(Document).options(selectinload(Document.lines)).where(
            Document.company_id==company_id,Document.id==package.stock_document_id)) if package.stock_document_id else None
        items=(await db.scalars(select(CounterpartyOpenItem).where(CounterpartyOpenItem.company_id==company_id,
            CounterpartyOpenItem.opening_balance_id==opening.id).order_by(CounterpartyOpenItem.id))).all()
        detail=dict(id=package.id,stock_document_id=package.stock_document_id,
            stock=[dict(product_id=line.product_id,warehouse_id=line.warehouse_id,quantity=line.quantity,unit_cost=line.price)
                for line in document.lines] if document else [],
            debts=[dict(open_item_id=item.id,reference=item.opening_reference,item_type=item.item_type,
                counterparty_id=item.counterparty_id,contract_id=item.contract_id,document_date=item.document_date,
                due_date=item.due_date,amount=item.original_amount,status=item.status) for item in items])
    return OpeningBalanceResponse(
        detail=detail,
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


from app.schemas.opening_balance_detail import OpeningDetailsCreate, OpeningPackageCreate
from app.services.opening_balance_detail_service import attach_opening_details, create_opening_package


@router.post('/packages/create', response_model=OpeningBalanceResponse, status_code=201,
    dependencies=[Depends(require_company_permission('journal_entries.create'))])
async def create_opening_package_endpoint(company_id: int, data: OpeningPackageCreate,
    current_user: User = Depends(require_company_permission('journal_entries.post')),
    db: AsyncSession = Depends(get_db)):
    try:
        opening=await create_opening_package(db,company_id=company_id,created_by=current_user.id,data=data)
        response=await _response(db,company_id,opening.id)
        await db.commit()
        return response
    except HTTPException:
        await db.rollback()
        raise
    except (OpeningBalanceError, AccountingPostingError, IntegrityError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(409, 'Opening package conflict' if isinstance(exc,IntegrityError) else str(exc)) from exc
    except Exception:
        await db.rollback()
        raise


@router.post('/{opening_balance_id}/details', response_model=OpeningBalanceResponse)
async def attach_opening_details_endpoint(company_id: int, opening_balance_id: int, data: OpeningDetailsCreate,
    current_user: User = Depends(require_company_permission('journal_entries.post')),
    db: AsyncSession = Depends(get_db)):
    try:
        opening=await attach_opening_details(db,company_id=company_id,opening_balance_id=opening_balance_id,
            created_by=current_user.id,data=data)
        response=await _response(db,company_id,opening.id)
        await db.commit()
        return response
    except OpeningBalanceNotFoundError as exc:
        await db.rollback()
        raise HTTPException(404,str(exc)) from exc
    except HTTPException:
        await db.rollback()
        raise
    except (OpeningBalanceError, IntegrityError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(409,'Opening detail conflict' if isinstance(exc,IntegrityError) else str(exc)) from exc
    except Exception:
        await db.rollback()
        raise
