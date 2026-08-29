from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import (
    require_company_permission,
)
from app.core.database import get_db
from app.models.counterparty_open_item import (
    CounterpartyOpenItem,
)
from app.schemas.counterparty_open_item import (
    CounterpartyOpenItemResponse,
)
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)
from app.services.payment_settlement_service import (
    PaymentSettlementDataIntegrityError,
    calculate_open_item_open_amount,
    get_active_open_item_settled_amounts,
)


def _open_item_response(
    item: CounterpartyOpenItem,
    *,
    settled_amount,
) -> CounterpartyOpenItemResponse:
    try:
        open_amount = (
            calculate_open_item_open_amount(
                original_amount=(
                    item.original_amount
                ),
                settled_amount=settled_amount,
                status=item.status,
            )
        )
    except PaymentSettlementDataIntegrityError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=str(exc),
        ) from exc

    return CounterpartyOpenItemResponse(
        id=item.id,
        company_id=item.company_id,
        trade_document_id=(
            item.trade_document_id
        ),
        counterparty_id=(
            item.counterparty_id
        ),
        contract_id=item.contract_id,
        item_type=item.item_type,
        status=item.status,
        document_date=item.document_date,
        due_date=item.due_date,
        currency_code=item.currency_code,
        original_amount=(
            item.original_amount
        ),
        settled_amount=settled_amount,
        open_amount=open_amount,
        created_at=item.created_at,
    )


router = APIRouter(
    prefix=(
        "/companies/{company_id}/"
        "counterparty-open-items"
    ),
    tags=["Counterparty Open Items"],
)


@router.get(
    "",
    response_model=list[
        CounterpartyOpenItemResponse
    ],
)
async def list_counterparty_open_items(
    company_id: int,
    item_type: CounterpartyOpenItemType | None = Query(
        default=None
    ),
    open_item_status: CounterpartyOpenItemStatus | None = Query(
        default=None,
        alias="status",
    ),
    counterparty_id: int | None = Query(
        default=None,
        gt=0,
    ),
    contract_id: int | None = Query(
        default=None,
        gt=0,
    ),
    currency_code: str | None = Query(
        default=None,
        min_length=3,
        max_length=3,
    ),
    document_date_from: date | None = Query(
        default=None,
    ),
    document_date_to: date | None = Query(
        default=None,
    ),
    due_date_from: date | None = Query(
        default=None,
    ),
    due_date_to: date | None = Query(
        default=None,
    ),
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "counterparty_open_items.read"
        )
    ),
):
    """
    List company-scoped AR/AP obligations.

    Cancelled items are intentionally included unless status filtering
    excludes them, preserving the financial audit trail.
    """
    if (
        document_date_from is not None
        and document_date_to is not None
        and document_date_from > document_date_to
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=(
                "document_date_from cannot be later "
                "than document_date_to"
            ),
        )

    if (
        due_date_from is not None
        and due_date_to is not None
        and due_date_from > due_date_to
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=(
                "due_date_from cannot be later "
                "than due_date_to"
            ),
        )

    statement = select(
        CounterpartyOpenItem
    ).where(
        CounterpartyOpenItem.company_id
        == company_id
    )

    if item_type is not None:
        statement = statement.where(
            CounterpartyOpenItem.item_type
            == item_type
        )

    if open_item_status is not None:
        statement = statement.where(
            CounterpartyOpenItem.status
            == open_item_status
        )

    if counterparty_id is not None:
        statement = statement.where(
            CounterpartyOpenItem.counterparty_id
            == counterparty_id
        )

    if contract_id is not None:
        statement = statement.where(
            CounterpartyOpenItem.contract_id
            == contract_id
        )

    if currency_code is not None:
        normalized_currency_code = (
            currency_code.strip().upper()
        )

        statement = statement.where(
            CounterpartyOpenItem.currency_code
            == normalized_currency_code
        )

    if document_date_from is not None:
        statement = statement.where(
            CounterpartyOpenItem.document_date
            >= document_date_from
        )

    if document_date_to is not None:
        statement = statement.where(
            CounterpartyOpenItem.document_date
            <= document_date_to
        )

    if due_date_from is not None:
        statement = statement.where(
            CounterpartyOpenItem.due_date
            >= due_date_from
        )

    if due_date_to is not None:
        statement = statement.where(
            CounterpartyOpenItem.due_date
            <= due_date_to
        )

    statement = statement.order_by(
        CounterpartyOpenItem.due_date.asc(),
        CounterpartyOpenItem.document_date.asc(),
        CounterpartyOpenItem.id.asc(),
    )

    result = await db.execute(
        statement
    )

    items = list(
        result.scalars().all()
    )

    settled = (
        await get_active_open_item_settled_amounts(
            db,
            company_id=company_id,
            open_item_ids=tuple(
                item.id
                for item in items
            ),
        )
    )

    return [
        _open_item_response(
            item,
            settled_amount=settled.get(
                item.id,
                0,
            ),
        )
        for item in items
    ]


@router.get(
    "/{open_item_id}",
    response_model=CounterpartyOpenItemResponse,
)
async def get_counterparty_open_item(
    company_id: int,
    open_item_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "counterparty_open_items.read"
        )
    ),
):
    """
    Read one company-scoped AR/AP obligation.
    """
    item = (
        await db.execute(
            select(
                CounterpartyOpenItem
            ).where(
                CounterpartyOpenItem.id
                == open_item_id,
                CounterpartyOpenItem.company_id
                == company_id,
            )
        )
    ).scalar_one_or_none()

    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Counterparty open item not found"
            ),
        )

    settled = (
        await get_active_open_item_settled_amounts(
            db,
            company_id=company_id,
            open_item_ids=(
                item.id,
            ),
        )
    )

    return _open_item_response(
        item,
        settled_amount=settled.get(
            item.id,
            0,
        ),
    )
