from dataclasses import dataclass
from datetime import date
from decimal import (
    Decimal,
    InvalidOperation,
)

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.purchase_value_correction_fifo_impact_calculation_service import (
    ActiveFifoConsumptionCandidate,
    FifoTransferRoutedDestinationSlice,
)
from app.services.purchase_value_correction_fifo_transfer_routing_service import (
    PurchaseValueCorrectionFifoTransferRoute,
    load_fifo_transfer_route_for_consumption,
)
from app.services.purchase_value_correction_fifo_transfer_slice_service import (
    build_fifo_transfer_destination_slices,
)
from app.services.purchase_value_correction_fifo_transfer_topology_service import (
    PurchaseValueCorrectionFifoTransferDestinationTopology,
    load_fifo_transfer_destination_topology,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionFifoTransferOrchestrationError(
    Exception
):
    """Base FIFO Transfer↔PVC orchestration error."""


class PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError(
    PurchaseValueCorrectionFifoTransferOrchestrationError
):
    """Loaded transfer provenance/topology is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class _PreloadedTransfer:
    route: PurchaseValueCorrectionFifoTransferRoute
    topology: (
        PurchaseValueCorrectionFifoTransferDestinationTopology
    )


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionFifoTransferRouter:
    """
    Immutable in-memory routing snapshot.

    All SQL/async loading happens before pure PVC
    calculation begins.

    route(...) is synchronous and performs no DB access.
    """

    _by_source_consumption_id: dict[
        int,
        _PreloadedTransfer,
    ]

    @property
    def transfer_consumption_ids(
        self,
    ) -> tuple[int, ...]:
        return tuple(
            sorted(
                self._by_source_consumption_id
            )
        )

    def route(
        self,
        *,
        source_consumption: ActiveFifoConsumptionCandidate,
        local_start: Decimal,
        local_end: Decimal,
        source_recognition_date: date,
    ) -> (
        tuple[
            FifoTransferRoutedDestinationSlice,
            ...,
        ]
        | None
    ):
        source_id = _positive_id(
            source_consumption.stock_lot_consumption_id,
            field=(
                "source StockLotConsumption id"
            ),
        )

        preloaded = (
            self._by_source_consumption_id.get(
                source_id
            )
        )

        if preloaded is None:
            return None

        physical_slices = (
            build_fifo_transfer_destination_slices(
                topology=preloaded.topology,
                local_start=local_start,
                local_end=local_end,
                source_recognition_date=(
                    source_recognition_date
                ),
            )
        )

        return tuple(
            FifoTransferRoutedDestinationSlice(
                stock_lot_id=row.stock_lot_id,
                destination_kind=(
                    row.destination_kind
                ),
                stock_lot_consumption_id=(
                    row.stock_lot_consumption_id
                ),
                issue_document_id=(
                    row.issue_document_id
                ),
                issue_document_line_id=(
                    row.issue_document_line_id
                ),
                quantity=row.quantity,
                recognition_date=(
                    row.recognition_date
                ),
            )
            for row in physical_slices
        )


def _positive_id(
    value,
    *,
    field: str,
) -> int:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
        or value <= 0
    ):
        raise (
            PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(value)
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise (
            PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


async def preload_purchase_value_correction_fifo_transfer_router(
    db: AsyncSession,
    *,
    company_id: int,
    active_consumptions: tuple[
        ActiveFifoConsumptionCandidate,
        ...,
    ],
) -> PurchaseValueCorrectionFifoTransferRouter:
    """
    Preload all warehouse-transfer routing state needed by
    one FIFO PVC calculation.

    Contract:
      * reads WTVL by source StockLotConsumption;
      * loads exact destination FIFO topology;
      * does no PVC persistence;
      * performs no FIFO mutation;
      * owns no COMMIT / ROLLBACK;
      * returns a synchronous callback-compatible router.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    preloaded: dict[
        int,
        _PreloadedTransfer,
    ] = {}

    validated_consumptions: list[
        tuple[
            int,
            ActiveFifoConsumptionCandidate,
        ]
    ] = []

    seen: set[int] = set()

    # Validate the complete caller-supplied set before
    # performing any database read. Invalid or duplicate
    # identities must fail without partial preload work.
    for consumption in active_consumptions:
        if not isinstance(
            consumption,
            ActiveFifoConsumptionCandidate,
        ):
            raise (
                PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError(
                    "active FIFO consumption has invalid type"
                )
            )

        consumption_id = _positive_id(
            consumption.stock_lot_consumption_id,
            field="StockLotConsumption id",
        )

        if consumption_id in seen:
            raise (
                PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError(
                    "duplicate active StockLotConsumption id"
                )
            )

        seen.add(
            consumption_id
        )

        validated_consumptions.append(
            (
                consumption_id,
                consumption,
            )
        )

    for (
        consumption_id,
        consumption,
    ) in validated_consumptions:
        route = (
            await load_fifo_transfer_route_for_consumption(
                db,
                company_id=company_id,
                stock_lot_consumption_id=(
                    consumption_id
                ),
            )
        )

        if route is None:
            continue

        if route.company_id != company_id:
            raise (
                PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError(
                    "transfer route company mismatch"
                )
            )

        if (
            route.source_stock_lot_consumption_id
            != consumption_id
        ):
            raise (
                PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError(
                    "transfer route source consumption mismatch"
                )
            )

        source_quantity = _decimal(
            consumption.quantity,
            field="source consumption quantity",
        )

        route_quantity = _decimal(
            route.quantity,
            field="transfer route quantity",
        )

        if (
            source_quantity <= ZERO
            or route_quantity <= ZERO
            or source_quantity != route_quantity
        ):
            raise (
                PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError(
                    "transfer route quantity does not match "
                    "source consumption quantity"
                )
            )

        topology = (
            await load_fifo_transfer_destination_topology(
                db,
                route=route,
            )
        )

        if (
            topology.route
            .source_stock_lot_consumption_id
            != consumption_id
        ):
            raise (
                PurchaseValueCorrectionFifoTransferOrchestrationIntegrityError(
                    "destination topology source mismatch"
                )
            )

        preloaded[
            consumption_id
        ] = _PreloadedTransfer(
            route=route,
            topology=topology,
        )

    return PurchaseValueCorrectionFifoTransferRouter(
        _by_source_consumption_id=preloaded
    )
