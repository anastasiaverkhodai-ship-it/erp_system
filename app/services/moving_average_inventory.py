from datetime import date, datetime, timezone
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.moving_average_balance import (
    MovingAverageBalance,
)


from app.models.moving_average_movement import (
    MovingAverageMovement,
)
from app.models.stock_ledger import StockMovementType

class MovingAverageInventoryError(Exception):
    """Business error raised by moving-average costing."""


async def get_locked_moving_average_balance(
    db: AsyncSession,
    company_id: int,
    product_id: int,
    warehouse_id: int,
) -> MovingAverageBalance:
    insert_statement = (
        insert(MovingAverageBalance.__table__)
        .values(
            company_id=company_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("0"),
            inventory_value=Decimal("0"),
            average_unit_cost=Decimal("0"),
            updated_at=datetime.now(
                timezone.utc
            ).replace(tzinfo=None),
        )
        .on_conflict_do_nothing(
            constraint=(
                "uq_moving_average_balance_"
                "company_product_warehouse"
            )
        )
    )

    await db.execute(
        insert_statement
    )

    result = await db.execute(
        select(MovingAverageBalance)
        .where(
            MovingAverageBalance.company_id
            == company_id,
            MovingAverageBalance.product_id
            == product_id,
            MovingAverageBalance.warehouse_id
            == warehouse_id,
        )
        .with_for_update()
    )

    balance = result.scalar_one_or_none()

    if balance is None:
        raise MovingAverageInventoryError(
            (
                "Moving average balance could not "
                "be created or locked"
            )
        )

    return balance

def _valuation_amount(
    value: Decimal,
) -> Decimal:
    return Decimal(value).quantize(
        Decimal("0.00000001"),
        rounding=ROUND_HALF_UP,
    )


async def process_moving_average_receipt(
    db: AsyncSession,
    company_id: int,
    document_id: int,
    document_line_id: int,
    product_id: int,
    warehouse_id: int,
    movement_date: date,
    quantity: Decimal,
    unit_cost: Decimal,
) -> MovingAverageBalance:
    quantity = Decimal(quantity)
    unit_cost = Decimal(unit_cost)

    if quantity <= Decimal("0"):
        raise MovingAverageInventoryError(
            "Receipt quantity must be greater than zero"
        )

    if unit_cost < Decimal("0"):
        raise MovingAverageInventoryError(
            "Receipt unit cost cannot be negative"
        )

    balance = await get_locked_moving_average_balance(
        db=db,
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
    )

    old_quantity = Decimal(
        balance.quantity
    )

    old_inventory_value = Decimal(
        balance.inventory_value
    )

    if (
        old_quantity == Decimal("0")
        and old_inventory_value != Decimal("0")
    ):
        raise MovingAverageInventoryError(
            (
                "Moving average balance is inconsistent: "
                "zero quantity has non-zero inventory value"
            )
        )

    receipt_value = _valuation_amount(
        quantity * unit_cost
    )

    new_quantity = (
        old_quantity
        + quantity
    )

    new_inventory_value = _valuation_amount(
        old_inventory_value
        + receipt_value
    )

    new_average_unit_cost = _valuation_amount(
        new_inventory_value
        / new_quantity
    )

    balance.quantity = new_quantity
    balance.inventory_value = (
        new_inventory_value
    )
    balance.average_unit_cost = (
        new_average_unit_cost
    )

    balance.updated_at = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    movement = MovingAverageMovement(
        company_id=company_id,
        document_id=document_id,
        document_line_id=document_line_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        movement_type=StockMovementType.RECEIPT,
        movement_date=movement_date,
        quantity_delta=quantity,
        value_delta=receipt_value,
        unit_cost=_valuation_amount(
            unit_cost
        ),
        balance_quantity_after=new_quantity,
        balance_value_after=new_inventory_value,
        average_unit_cost_after=(
            new_average_unit_cost
        ),
    )

    db.add(movement)
    return balance

async def process_moving_average_issue(
    db: AsyncSession,
    company_id: int,
    document_id: int,
    document_line_id: int,
    product_id: int,
    warehouse_id: int,
    movement_date: date,
    quantity: Decimal,
) -> Decimal:
    quantity = Decimal(quantity)

    if quantity <= Decimal("0"):
        raise MovingAverageInventoryError(
            "Issue quantity must be greater than zero"
        )

    balance = await get_locked_moving_average_balance(
        db=db,
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
    )

    old_quantity = Decimal(
        balance.quantity
    )

    old_inventory_value = Decimal(
        balance.inventory_value
    )

    old_average_unit_cost = Decimal(
        balance.average_unit_cost
    )

    if old_quantity <= Decimal("0"):
        raise MovingAverageInventoryError(
            "No moving-average inventory is available"
        )

    if quantity > old_quantity:
        raise MovingAverageInventoryError(
            (
                "Insufficient moving-average inventory. "
                f"Available: {old_quantity}, "
                f"required: {quantity}"
            )
        )

    if old_inventory_value < Decimal("0"):
        raise MovingAverageInventoryError(
            (
                "Moving average balance is inconsistent: "
                "inventory value is negative"
            )
        )

    if quantity == old_quantity:
        issue_cost = _valuation_amount(
            old_inventory_value
        )

        new_quantity = Decimal("0")
        new_inventory_value = Decimal("0")
        new_average_unit_cost = Decimal("0")

    else:
        issue_cost = _valuation_amount(
            quantity
            * old_average_unit_cost
        )

        new_quantity = (
            old_quantity
            - quantity
        )

        new_inventory_value = _valuation_amount(
            old_inventory_value
            - issue_cost
        )

        if new_inventory_value < Decimal("0"):
            raise MovingAverageInventoryError(
                (
                    "Moving average inventory value "
                    "would become negative"
                )
            )

        new_average_unit_cost = (
            old_average_unit_cost
        )

    balance.quantity = new_quantity

    balance.inventory_value = (
        new_inventory_value
    )

    balance.average_unit_cost = (
        new_average_unit_cost
    )

    balance.updated_at = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    effective_issue_unit_cost = _valuation_amount(
        issue_cost / quantity
    )

    movement = MovingAverageMovement(
        company_id=company_id,
        document_id=document_id,
        document_line_id=document_line_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        movement_type=StockMovementType.ISSUE,
        movement_date=movement_date,
        quantity_delta=-quantity,
        value_delta=-issue_cost,
        unit_cost=effective_issue_unit_cost,
        balance_quantity_after=new_quantity,
        balance_value_after=new_inventory_value,
        average_unit_cost_after=(
            new_average_unit_cost
        ),
    )

    db.add(movement)
    return issue_cost

async def reverse_moving_average_document(
    db: AsyncSession,
    company_id: int,
    document_id: int,
    reversal_date: date,
) -> None:
    movements_result = await db.execute(
        select(MovingAverageMovement)
        .where(
            MovingAverageMovement.company_id
            == company_id,
            MovingAverageMovement.document_id
            == document_id,
            MovingAverageMovement.movement_type
            != StockMovementType.REVERSAL,
        )
        .order_by(MovingAverageMovement.id)
        .with_for_update()
    )

    original_movements = (
        movements_result.scalars().all()
    )

    if not original_movements:
        raise MovingAverageInventoryError(
            (
                "Document has no moving-average "
                "movements to reverse"
            )
        )

    original_movement_ids = [
        movement.id
        for movement in original_movements
    ]

    existing_reversal_result = await db.execute(
        select(MovingAverageMovement.id)
        .where(
            MovingAverageMovement.reversal_of_id.in_(
                original_movement_ids
            )
        )
        .limit(1)
    )

    if (
        existing_reversal_result.scalar_one_or_none()
        is not None
    ):
        raise MovingAverageInventoryError(
            (
                "Moving-average movements have "
                "already been reversed"
            )
        )

    movements_by_key: dict[
        tuple[int, int],
        list[MovingAverageMovement],
    ] = {}

    for movement in original_movements:
        key = (
            movement.product_id,
            movement.warehouse_id,
        )

        movements_by_key.setdefault(
            key,
            [],
        ).append(
            movement
        )

    for (
        product_id,
        warehouse_id,
    ), movements in sorted(
        movements_by_key.items()
    ):
        movements = sorted(
            movements,
            key=lambda item: item.id,
        )

        latest_result = await db.execute(
            select(MovingAverageMovement)
            .where(
                MovingAverageMovement.company_id
                == company_id,
                MovingAverageMovement.product_id
                == product_id,
                MovingAverageMovement.warehouse_id
                == warehouse_id,
            )
            .order_by(
                MovingAverageMovement.id.desc()
            )
            .limit(1)
            .with_for_update()
        )

        latest_movement = (
            latest_result.scalar_one_or_none()
        )

        if (
            latest_movement is None
            or latest_movement.id
            != movements[-1].id
        ):
            raise MovingAverageInventoryError(
                (
                    "Cannot reverse moving-average "
                    "document because later inventory "
                    "movements exist for product "
                    f"{product_id}, warehouse "
                    f"{warehouse_id}"
                )
            )

        balance = (
            await get_locked_moving_average_balance(
                db=db,
                company_id=company_id,
                product_id=product_id,
                warehouse_id=warehouse_id,
            )
        )

        for movement in reversed(
            movements
        ):
            if (
                Decimal(balance.quantity)
                != Decimal(
                    movement.balance_quantity_after
                )
                or Decimal(
                    balance.inventory_value
                )
                != Decimal(
                    movement.balance_value_after
                )
                or Decimal(
                    balance.average_unit_cost
                )
                != Decimal(
                    movement.average_unit_cost_after
                )
            ):
                raise MovingAverageInventoryError(
                    (
                        "Moving-average balance does "
                        "not match movement history"
                    )
                )

            previous_result = await db.execute(
                select(MovingAverageMovement)
                .where(
                    MovingAverageMovement.company_id
                    == company_id,
                    MovingAverageMovement.product_id
                    == product_id,
                    MovingAverageMovement.warehouse_id
                    == warehouse_id,
                    MovingAverageMovement.id
                    < movement.id,
                )
                .order_by(
                    MovingAverageMovement.id.desc()
                )
                .limit(1)
            )

            previous_movement = (
                previous_result.scalar_one_or_none()
            )

            if previous_movement is None:
                restored_quantity = Decimal("0")
                restored_value = Decimal("0")
                restored_average = Decimal("0")

            else:
                restored_quantity = Decimal(
                    previous_movement.balance_quantity_after
                )

                restored_value = Decimal(
                    previous_movement.balance_value_after
                )

                restored_average = Decimal(
                    previous_movement.average_unit_cost_after
                )

            reversal_quantity_delta = (
                -Decimal(
                    movement.quantity_delta
                )
            )

            reversal_value_delta = (
                -Decimal(
                    movement.value_delta
                )
            )

            balance.quantity = (
                restored_quantity
            )

            balance.inventory_value = (
                restored_value
            )

            balance.average_unit_cost = (
                restored_average
            )

            balance.updated_at = datetime.now(
                timezone.utc
            ).replace(tzinfo=None)

            reversal_movement = (
                MovingAverageMovement(
                    company_id=company_id,
                    document_id=document_id,
                    document_line_id=(
                        movement.document_line_id
                    ),
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                    movement_type=(
                        StockMovementType.REVERSAL
                    ),
                    movement_date=reversal_date,
                    quantity_delta=(
                        reversal_quantity_delta
                    ),
                    value_delta=(
                        reversal_value_delta
                    ),
                    unit_cost=Decimal(
                        movement.unit_cost
                    ),
                    balance_quantity_after=(
                        restored_quantity
                    ),
                    balance_value_after=(
                        restored_value
                    ),
                    average_unit_cost_after=(
                        restored_average
                    ),
                    reversal_of_id=movement.id,
                )
            )

            db.add(
                reversal_movement
            )