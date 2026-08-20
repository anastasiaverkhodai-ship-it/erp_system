from decimal import Decimal

from app.services.reservation_balance import (
    ReservationBalance,
)
from app.services.reservation_movement_catalog import (
    ReservationMovementCatalog,
)


def build_reservation_balance(
    physical_quantity: Decimal,
    company_id: int,
    product_id: int,
    warehouse_id: int,
    catalog: ReservationMovementCatalog,
) -> ReservationBalance:
    """
    Build a reservation balance from physical stock
    and accumulated reservation movements.
    """

    reserved_quantity = catalog.reserved_quantity(
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
    )

    return ReservationBalance(
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        physical_quantity=physical_quantity,
        reserved_quantity=reserved_quantity,
    )