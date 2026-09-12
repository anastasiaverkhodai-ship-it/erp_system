from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import InventoryValuationMethod
from app.models.warehouse_transfer_event import (
    WarehouseTransferEvent,
)
from app.services.warehouse_transfer_history_service import (
    WarehouseTransferTarget,
)


@dataclass(frozen=True)
class WarehouseTransferValuationLayerResult:
    valuation_method: InventoryValuationMethod
    quantity: Decimal
    unit_cost: Decimal
    valuation_amount: Decimal
    source_inventory_cost_entry_id: int
    source_stock_lot_consumption_id: int | None
    destination_receipt_document_id: int
    destination_receipt_document_line_id: int


@dataclass(frozen=True)
class WarehouseTransferPhysicalLine:
    """
    One BUSINESS transfer line.

    Exactly one source ISSUE line.
    One destination RECEIPT document.
    N destination technical receipt lines through valuation_layers.
    """

    product_id: int
    quantity: Decimal

    issue_document_id: int
    issue_document_line_id: int

    receipt_document_id: int

    valuation_layers: tuple[
        WarehouseTransferValuationLayerResult,
        ...
    ]


@dataclass(frozen=True)
class WarehouseTransferPhysicalResult:
    lines: tuple[
        WarehouseTransferPhysicalLine,
        ...
    ]


class WarehouseTransferPhysicalFactory(Protocol):
    async def create_transfer(
        self,
        db: AsyncSession,
        *,
        target: WarehouseTransferTarget,
        created_by: int,
    ) -> WarehouseTransferPhysicalResult:
        ...

    async def reverse_transfer(
        self,
        db: AsyncSession,
        *,
        original_event: WarehouseTransferEvent,
        reversal_date,
        created_by: int,
    ) -> None:
        ...


class DefaultWarehouseTransferPhysicalFactory:
    """
    Real internal warehouse-transfer physical factory.

    CREATE chronology:
      1. create source ISSUE document + business lines;
      2. warehouse-only post source ISSUE;
      3. flush immutable source costing provenance;
      4. load exact source ICE / FIFO consumptions;
      5. create destination RECEIPT document;
      6. FIFO -> one technical receipt line per source layer;
         MA   -> one technical receipt line with exact Q8 override;
      7. warehouse-only post destination receipt;
      8. return complete immutable physical provenance.

    No accounting handler.
    No JournalEntry.
    No VAT.
    No commit / rollback.

    Reversal is intentionally not implemented in this phase.
    """

    async def create_transfer(
        self,
        db: AsyncSession,
        *,
        target,
        created_by: int,
    ) -> WarehouseTransferPhysicalResult:
        from decimal import (
            Decimal,
            ROUND_HALF_UP,
        )
        from uuid import uuid4

        from app.models.company import (
            InventoryValuationMethod,
        )
        from app.models.document import (
            Document,
            DocumentStatus,
            DocumentType,
        )
        from app.models.document_line import (
            DocumentLine,
        )
        from app.services.warehouse_transfer_posting_service import (
            WarehouseTransferPostingError,
            post_warehouse_transfer_document,
        )
        from app.services.warehouse_transfer_source_valuation_loader import (
            WarehouseTransferSourceValuationLoaderError,
            load_warehouse_transfer_source_valuation,
        )

        try:
            company_id = target.company_id
            source_warehouse_id = (
                target.source_warehouse_id
            )
            destination_warehouse_id = (
                target.destination_warehouse_id
            )
            transfer_date = target.transfer_date
            target_lines = tuple(
                target.lines
            )
        except AttributeError as exc:
            raise ValueError(
                "transfer target does not satisfy "
                "history/executor contract"
            ) from exc

        if not target_lines:
            raise ValueError(
                "transfer target has no lines"
            )

        # ---------------------------------------------
        # SOURCE ISSUE DOCUMENT
        # ---------------------------------------------

        issue_document = Document(
            company_id=company_id,
            accounting_rule_id=None,
            number=(
                "WT-I-"
                + uuid4().hex
            ),
            document_type=DocumentType.ISSUE,
            document_date=transfer_date,
            status=DocumentStatus.DRAFT,
            created_by=created_by,
        )

        db.add(
            issue_document
        )
        await db.flush()

        if issue_document.id is None:
            raise ValueError(
                "transfer ISSUE document did not receive an ID"
            )

        issue_lines_by_product = {}

        for target_line in target_lines:
            product_id = (
                target_line.product_id
            )
            quantity = Decimal(
                target_line.quantity
            )

            if product_id in issue_lines_by_product:
                raise ValueError(
                    "duplicate product in transfer target"
                )

            issue_line = DocumentLine(
                document_id=issue_document.id,
                product_id=product_id,
                warehouse_id=source_warehouse_id,
                quantity=quantity,
                # ISSUE costing does not use document price.
                # Keep technical price neutral.
                price=Decimal("0"),
            )

            # Persist explicitly instead of touching the
            # unloaded Document.lines relationship.
            #
            # In AsyncSession, accessing an unloaded relationship
            # here can trigger implicit lazy IO and MissingGreenlet.
            # document_id already carries the required ownership.
            db.add(
                issue_line
            )

            issue_lines_by_product[
                product_id
            ] = issue_line

        await db.flush()

        for issue_line in issue_lines_by_product.values():
            if issue_line.id is None:
                raise ValueError(
                    "transfer ISSUE line did not receive an ID"
                )

        try:
            await post_warehouse_transfer_document(
                db,
                document=issue_document,
                created_by=created_by,
            )
        except WarehouseTransferPostingError as exc:
            raise ValueError(
                f"source transfer ISSUE failed: {exc}"
            ) from exc

        # Costing rows need generated PKs before provenance loading.
        await db.flush()

        # ---------------------------------------------
        # LOAD IMMUTABLE SOURCE VALUATION
        # ---------------------------------------------

        valuation_by_product = {}

        for (
            product_id,
            issue_line,
        ) in issue_lines_by_product.items():
            try:
                valuation = (
                    await load_warehouse_transfer_source_valuation(
                        db,
                        company_id=company_id,
                        issue_document_id=issue_document.id,
                        issue_document_line_id=issue_line.id,
                    )
                )
            except WarehouseTransferSourceValuationLoaderError as exc:
                raise ValueError(
                    "source transfer valuation loading failed: "
                    f"{exc}"
                ) from exc

            valuation_by_product[
                product_id
            ] = valuation

        # ---------------------------------------------
        # DESTINATION RECEIPT DOCUMENT
        # ---------------------------------------------

        receipt_document = Document(
            company_id=company_id,
            accounting_rule_id=None,
            number=(
                "WT-R-"
                + uuid4().hex
            ),
            document_type=DocumentType.RECEIPT,
            document_date=transfer_date,
            status=DocumentStatus.DRAFT,
            created_by=created_by,
        )

        db.add(
            receipt_document
        )
        await db.flush()

        if receipt_document.id is None:
            raise ValueError(
                "transfer RECEIPT document did not receive an ID"
            )

        receipt_layers_by_product = {}
        exact_values = {}

        PRICE_Q4 = Decimal(
            "0.0001"
        )

        for target_line in target_lines:
            product_id = (
                target_line.product_id
            )

            valuation = (
                valuation_by_product[
                    product_id
                ]
            )

            receipt_rows = []

            if (
                valuation.valuation_method
                == InventoryValuationMethod.FIFO
            ):
                # Preserve each exact historical FIFO layer.
                for layer in valuation.layers:
                    receipt_line = DocumentLine(
                        document_id=receipt_document.id,
                        product_id=product_id,
                        warehouse_id=destination_warehouse_id,
                        quantity=layer.quantity,
                        price=layer.unit_cost,
                    )

                    # Persist explicitly instead of touching the
                    # unloaded Document.lines relationship.
                    db.add(
                        receipt_line
                    )

                    receipt_rows.append(
                        (
                            layer,
                            receipt_line,
                        )
                    )

            elif (
                valuation.valuation_method
                == InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
            ):
                if len(
                    valuation.layers
                ) != 1:
                    raise ValueError(
                        "moving-average transfer requires "
                        "exactly one valuation layer"
                    )

                layer = (
                    valuation.layers[0]
                )

                # DocumentLine.price is only Q4.
                # Exact valuation remains source ICE Q8.
                display_price = Decimal(
                    layer.unit_cost
                ).quantize(
                    PRICE_Q4,
                    rounding=ROUND_HALF_UP,
                )

                receipt_line = DocumentLine(
                    document_id=receipt_document.id,
                    product_id=product_id,
                    warehouse_id=destination_warehouse_id,
                    quantity=layer.quantity,
                    price=display_price,
                )

                # Persist explicitly instead of touching the
                # unloaded Document.lines relationship.
                db.add(
                    receipt_line
                )

                receipt_rows.append(
                    (
                        layer,
                        receipt_line,
                    )
                )

            else:
                raise ValueError(
                    "unsupported transfer valuation method"
                )

            receipt_layers_by_product[
                product_id
            ] = receipt_rows

        await db.flush()

        for (
            product_id,
            receipt_rows,
        ) in receipt_layers_by_product.items():
            valuation = (
                valuation_by_product[
                    product_id
                ]
            )

            for (
                layer,
                receipt_line,
            ) in receipt_rows:
                if receipt_line.id is None:
                    raise ValueError(
                        "transfer RECEIPT line did not receive an ID"
                    )

                if (
                    valuation.valuation_method
                    == InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
                ):
                    exact_values[
                        receipt_line.id
                    ] = Decimal(
                        layer.valuation_amount
                    )

        # ---------------------------------------------
        # DESTINATION WAREHOUSE-ONLY POSTING
        # ---------------------------------------------

        try:
            await post_warehouse_transfer_document(
                db,
                document=receipt_document,
                created_by=created_by,
                exact_receipt_valuation_amounts=(
                    exact_values
                    if exact_values
                    else None
                ),
            )
        except WarehouseTransferPostingError as exc:
            raise ValueError(
                "destination transfer RECEIPT failed: "
                f"{exc}"
            ) from exc

        await db.flush()

        # ---------------------------------------------
        # TRANSFER-SPECIFIC DESTINATION MA RECEIPT ICE
        # ---------------------------------------------
        #
        # Generic MA RECEIPT posting owns quantity/value state,
        # but does not persist InventoryCostEntry provenance.
        #
        # Transfer↔PVC routing requires this immutable chain:
        #
        #   source ISSUE ICE
        #       -> exact transfer valuation layer
        #       -> destination RECEIPT document/line
        #       -> destination RECEIPT ICE
        #
        # Exact source-derived Q8 layer values are authoritative.
        # DocumentLine.price remains Q4 display data only.
        #
        # No JournalEntry / VAT.
        # Caller retains transaction ownership.
        from app.models.inventory_cost_entry import (
            InventoryCostEntry,
        )

        MONEY_Q2 = Decimal("0.01")

        for (
            product_id,
            receipt_rows,
        ) in receipt_layers_by_product.items():
            valuation = (
                valuation_by_product[
                    product_id
                ]
            )

            if (
                valuation.valuation_method
                != InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
            ):
                continue

            if len(receipt_rows) != 1:
                raise ValueError(
                    "moving-average transfer requires exactly "
                    "one destination receipt row"
                )

            (
                layer,
                receipt_line,
            ) = receipt_rows[0]

            if receipt_line.id is None:
                raise ValueError(
                    "transfer RECEIPT line did not receive an ID"
                )

            quantity = Decimal(
                layer.quantity
            )

            unit_cost = Decimal(
                layer.unit_cost
            )

            valuation_amount = Decimal(
                layer.valuation_amount
            )

            if quantity <= 0:
                raise ValueError(
                    "destination transfer RECEIPT ICE quantity "
                    "must be positive"
                )

            if unit_cost < 0:
                raise ValueError(
                    "destination transfer RECEIPT ICE unit cost "
                    "cannot be negative"
                )

            if valuation_amount < 0:
                raise ValueError(
                    "destination transfer RECEIPT ICE valuation "
                    "cannot be negative"
                )

            destination_cost_entry = InventoryCostEntry(
                company_id=company_id,
                document_id=receipt_document.id,
                document_line_id=receipt_line.id,
                valuation_method=(
                    InventoryValuationMethod
                    .WEIGHTED_AVERAGE_MOVING
                ),
                quantity=quantity,
                unit_cost=unit_cost,
                valuation_amount=valuation_amount,
                cost_amount=(
                    valuation_amount.quantize(
                        MONEY_Q2,
                        rounding=ROUND_HALF_UP,
                    )
                ),
            )

            db.add(
                destination_cost_entry
            )

        # Destination MA movement and destination immutable ICE
        # must both exist before physical transfer provenance
        # is handed to the reconciliation executor.
        await db.flush()


        # ---------------------------------------------
        # RETURN FULL PHYSICAL PROVENANCE
        # ---------------------------------------------

        physical_lines = []

        for target_line in target_lines:
            product_id = (
                target_line.product_id
            )

            issue_line = (
                issue_lines_by_product[
                    product_id
                ]
            )

            valuation = (
                valuation_by_product[
                    product_id
                ]
            )

            receipt_rows = (
                receipt_layers_by_product[
                    product_id
                ]
            )

            result_layers = []

            for (
                layer,
                receipt_line,
            ) in receipt_rows:
                result_layers.append(
                    WarehouseTransferValuationLayerResult(
                        valuation_method=(
                            layer.valuation_method
                        ),
                        quantity=layer.quantity,
                        unit_cost=layer.unit_cost,
                        valuation_amount=(
                            layer.valuation_amount
                        ),
                        source_inventory_cost_entry_id=(
                            layer.source_inventory_cost_entry_id
                        ),
                        source_stock_lot_consumption_id=(
                            layer.source_stock_lot_consumption_id
                        ),
                        destination_receipt_document_id=(
                            receipt_document.id
                        ),
                        destination_receipt_document_line_id=(
                            receipt_line.id
                        ),
                    )
                )

            physical_lines.append(
                WarehouseTransferPhysicalLine(
                    product_id=product_id,
                    quantity=Decimal(
                        target_line.quantity
                    ),
                    issue_document_id=(
                        issue_document.id
                    ),
                    issue_document_line_id=(
                        issue_line.id
                    ),
                    receipt_document_id=(
                        receipt_document.id
                    ),
                    valuation_layers=tuple(
                        result_layers
                    ),
                )
            )

        return WarehouseTransferPhysicalResult(
            lines=tuple(
                physical_lines
            )
        )

    async def reverse_transfer(
        self,
        db: AsyncSession,
        *,
        original_event,
        reversal_date,
        created_by: int,
    ) -> None:
        """
        Reverse one exact persisted warehouse transfer.

        Physical chronology:

            destination RECEIPT
            -> source ISSUE

        Caller owns COMMIT / ROLLBACK.
        """
        from decimal import Decimal

        from sqlalchemy import select

        from app.models.warehouse_transfer_event import (
            WarehouseTransferEvent,
        )
        from app.models.warehouse_transfer_line import (
            WarehouseTransferLine,
        )
        from app.models.warehouse_transfer_valuation_layer import (
            WarehouseTransferValuationLayer,
        )
        from app.services.warehouse_transfer_posting_service import (
            WarehouseTransferPostingError,
            reverse_warehouse_transfer_document,
        )

        event_id = getattr(
            original_event,
            "id",
            None,
        )

        target = getattr(
            original_event,
            "target",
            None,
        )

        if (
            isinstance(event_id, bool)
            or not isinstance(event_id, int)
            or event_id <= 0
        ):
            raise ValueError(
                "persisted original transfer event id is required"
            )

        if target is None:
            raise ValueError(
                "active original transfer target is required"
            )

        company_id = getattr(
            target,
            "company_id",
            None,
        )

        if (
            isinstance(company_id, bool)
            or not isinstance(company_id, int)
            or company_id <= 0
        ):
            raise ValueError(
                "original transfer company_id is invalid"
            )

        # -------------------------------------------------
        # LOCK EXACT PERSISTED TRANSFER EVENT
        # -------------------------------------------------

        event_result = await db.execute(
            select(
                WarehouseTransferEvent
            )
            .where(
                WarehouseTransferEvent.id
                == event_id,
                WarehouseTransferEvent.company_id
                == company_id,
            )
            .with_for_update()
        )

        event = event_result.scalar_one_or_none()

        if event is None:
            raise ValueError(
                "persisted transfer event not found"
            )

        if event.reversal_of_id is not None:
            raise ValueError(
                "cannot physically reverse a reversal event"
            )

        if (
            event.source_warehouse_id
            != target.source_warehouse_id
            or event.destination_warehouse_id
            != target.destination_warehouse_id
            or event.transfer_date
            != target.transfer_date
        ):
            raise ValueError(
                "persisted transfer event does not match "
                "active history target"
            )

        # -------------------------------------------------
        # LOAD BUSINESS TRANSFER LINES
        # -------------------------------------------------

        lines_result = await db.execute(
            select(
                WarehouseTransferLine
            )
            .where(
                WarehouseTransferLine.company_id
                == company_id,
                WarehouseTransferLine.transfer_event_id
                == event.id,
            )
            .order_by(
                WarehouseTransferLine.product_id,
                WarehouseTransferLine.id,
            )
        )

        lines = tuple(
            lines_result.scalars().all()
        )

        if not lines:
            raise ValueError(
                "persisted transfer has no business lines"
            )

        line_ids = tuple(
            line.id
            for line in lines
        )

        if any(
            line_id is None
            for line_id in line_ids
        ):
            raise ValueError(
                "persisted transfer line id is required"
            )

        issue_document_ids = {
            line.issue_document_id
            for line in lines
        }

        receipt_document_ids = {
            line.receipt_document_id
            for line in lines
        }

        if len(issue_document_ids) != 1:
            raise ValueError(
                "transfer provenance must reference exactly "
                "one source ISSUE document"
            )

        if len(receipt_document_ids) != 1:
            raise ValueError(
                "transfer provenance must reference exactly "
                "one destination RECEIPT document"
            )

        issue_document_id = next(
            iter(issue_document_ids)
        )

        receipt_document_id = next(
            iter(receipt_document_ids)
        )

        if (
            issue_document_id
            == receipt_document_id
        ):
            raise ValueError(
                "source and destination documents must differ"
            )

        # -------------------------------------------------
        # LOAD IMMUTABLE VALUATION PROVENANCE
        # -------------------------------------------------

        layers_result = await db.execute(
            select(
                WarehouseTransferValuationLayer
            )
            .where(
                WarehouseTransferValuationLayer.company_id
                == company_id,
                WarehouseTransferValuationLayer.transfer_line_id.in_(
                    line_ids
                ),
            )
            .order_by(
                WarehouseTransferValuationLayer.transfer_line_id,
                WarehouseTransferValuationLayer.id,
            )
        )

        layers = tuple(
            layers_result.scalars().all()
        )

        if not layers:
            raise ValueError(
                "persisted transfer has no valuation layers"
            )

        lines_by_id = {
            line.id: line
            for line in lines
        }

        layers_by_line = {
            line.id: []
            for line in lines
        }

        destination_receipt_line_ids = set()
        source_fifo_consumption_ids = set()

        for layer in layers:
            line = lines_by_id.get(
                layer.transfer_line_id
            )

            if line is None:
                raise ValueError(
                    "valuation layer belongs to another "
                    "transfer business line"
                )

            if (
                layer.company_id != company_id
                or layer.product_id != line.product_id
                or (
                    layer.destination_warehouse_id
                    != line.destination_warehouse_id
                )
                or (
                    layer.destination_receipt_document_id
                    != line.receipt_document_id
                )
            ):
                raise ValueError(
                    "valuation layer identity does not match "
                    "transfer business line"
                )

            destination_line_id = (
                layer.destination_receipt_document_line_id
            )

            if (
                destination_line_id
                in destination_receipt_line_ids
            ):
                raise ValueError(
                    "duplicate destination receipt line "
                    "in transfer valuation provenance"
                )

            destination_receipt_line_ids.add(
                destination_line_id
            )

            source_fifo_id = (
                layer.source_stock_lot_consumption_id
            )

            if source_fifo_id is not None:
                if (
                    source_fifo_id
                    in source_fifo_consumption_ids
                ):
                    raise ValueError(
                        "duplicate source FIFO consumption "
                        "in transfer valuation provenance"
                    )

                source_fifo_consumption_ids.add(
                    source_fifo_id
                )

            layers_by_line[
                line.id
            ].append(
                layer
            )

        for line in lines:
            line_layers = layers_by_line[
                line.id
            ]

            if not line_layers:
                raise ValueError(
                    "every transfer business line requires "
                    "valuation provenance"
                )

            layer_quantity = sum(
                (
                    Decimal(layer.quantity)
                    for layer in line_layers
                ),
                Decimal("0"),
            )

            if (
                layer_quantity
                != Decimal(line.quantity)
            ):
                raise ValueError(
                    "transfer valuation layer quantity does "
                    "not equal business transfer quantity"
                )

        # -------------------------------------------------
        # CRITICAL PHYSICAL REVERSAL ORDER
        #
        # Destination first:
        # - FIFO blocks consumed destination lots.
        # - MA blocks later destination movement.
        #
        # Only after destination succeeds may source stock
        # be restored.
        # -------------------------------------------------

        try:
            await reverse_warehouse_transfer_document(
                db,
                company_id=company_id,
                document_id=receipt_document_id,
                reversal_date=reversal_date,
                reversed_by=created_by,
            )
        except WarehouseTransferPostingError as exc:
            raise ValueError(
                "destination transfer RECEIPT reversal failed: "
                f"{exc}"
            ) from exc

        try:
            await reverse_warehouse_transfer_document(
                db,
                company_id=company_id,
                document_id=issue_document_id,
                reversal_date=reversal_date,
                reversed_by=created_by,
            )
        except WarehouseTransferPostingError as exc:
            raise ValueError(
                "source transfer ISSUE reversal failed: "
                f"{exc}"
            ) from exc

        await db.flush()
