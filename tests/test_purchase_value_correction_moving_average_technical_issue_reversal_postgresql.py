import importlib.util
import os
from pathlib import Path
import sys
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import engine
from app.models.company import InventoryValuationMethod
from app.services.reservation_persistence_service import (
    reserve_source_line,
)
from app.services.sales_return_operational_service import (
    apply_sales_return_operational_event,
)
from app.services.trade_fulfillment_service import (
    SalesOrderFulfillmentRequestLine,
    execute_sales_order_fulfillment,
)

from app.models.trade_value_correction_event import (
    TradeValueCorrectionEvent,
)
from app.services.invoice_fulfillment_allocation_service import (
    create_invoice_fulfillment_allocation,
)
from app.services.purchase_value_correction_allocation_reconciliation_service import (
    reconcile_purchase_value_correction_allocations_for_event,
)
from app.services.purchase_value_correction_moving_average_lifecycle_service import (
    reconcile_and_post_purchase_value_correction_moving_average_peers,
)
from app.services.purchase_value_correction_moving_average_peer_reconciliation_service import (
    reconcile_purchase_value_correction_moving_average_peers,
)


RUN_POSTGRES_E2E = (
    os.getenv(
        "RUN_POSTGRES_E2E"
    )
    == "1"
)

COMPANY_ID = 1
USER_ID = 1

ZERO = Decimal("0.00000000")


pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run real PostgreSQL chronology"
    ),
)


def _load_fifo_pg_helpers():
    """
    Reuse the already-proven real PostgreSQL business fixture.

    The helper module owns:
    - purchase business fixture;
    - real purchase fulfillment;
    - accounting-period setup;
    - sales-order creation;
    - accounting-rule seeding;
    - baseline counting.

    This MA test changes only the transaction-local company
    inventory valuation method before warehouse posting.

    Nothing is committed.
    """

    path = Path(
        __file__
    ).with_name(
        "test_purchase_value_correction_fifo_impact_"
        "postgresql_chronology.py"
    )

    spec = importlib.util.spec_from_file_location(
        "_pvc_fifo_pg_helpers_for_ma",
        path,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "Could not load proven FIFO PostgreSQL helper module"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    sys.modules[
        spec.name
    ] = module

    spec.loader.exec_module(
        module
    )

    return module


base = _load_fifo_pg_helpers()


async def scalar(
    db,
    sql,
    params=None,
):
    return (
        await db.execute(
            text(
                sql
            ),
            params or {},
        )
    ).scalar_one()


async def scalar_or_none(
    db,
    sql,
    params=None,
):
    return (
        await db.execute(
            text(
                sql
            ),
            params or {},
        )
    ).scalar_one_or_none()


async def mapping_one(
    db,
    sql,
    params=None,
):
    return (
        await db.execute(
            text(
                sql
            ),
            params or {},
        )
    ).mappings().one()


async def rows(
    db,
    sql,
    params=None,
):
    return tuple(
        (
            await db.execute(
                text(
                    sql
                ),
                params or {},
            )
        ).mappings().all()
    )


async def complete_table_counts():
    """
    Reuse the proven rollback baseline and add MA/PVC-MA tables
    that are specific to this chronology.
    """

    existing = (
        await base.complete_table_counts()
    )

    extra_names = (
        "moving_average_balances",
        "moving_average_movements",
        "inventory_cost_entries",
        "purchase_value_correction_ma_replay_events",
        "journal_entries",
        "journal_entry_lines",
    )

    extra = {}

    async with engine.connect() as connection:
        for table_name in extra_names:
            extra[
                table_name
            ] = (
                await connection.execute(
                    text(
                        f"""
                        SELECT COUNT(*)
                        FROM {table_name}
                        """
                    )
                )
            ).scalar_one()

    return {
        "base": existing,
        "ma": extra,
    }


async def set_company_to_moving_average(
    db,
):
    current = await scalar(
        db,
        """
        SELECT inventory_valuation_method
        FROM companies
        WHERE id = :company_id
          AND is_active IS TRUE
        """,
        {
            "company_id":
                COMPANY_ID,
        },
    )

    assert current in (
        "fifo",
        "weighted_average_moving",
    )

    await db.execute(
        text(
            """
            UPDATE companies
            SET inventory_valuation_method =
                :valuation_method
            WHERE id = :company_id
              AND is_active IS TRUE
            """
        ),
        {
            "company_id":
                COMPANY_ID,
            "valuation_method": (
                InventoryValuationMethod
                .WEIGHTED_AVERAGE_MOVING
                .value
            ),
        },
    )

    await db.flush()

    actual = await scalar(
        db,
        """
        SELECT inventory_valuation_method
        FROM companies
        WHERE id = :company_id
        """,
        {
            "company_id":
                COMPANY_ID,
        },
    )

    assert (
        actual
        == "weighted_average_moving"
    )


async def ma_balance(
    db,
    *,
    product_id,
    warehouse_id,
):
    return await mapping_one(
        db,
        """
        SELECT
            quantity,
            inventory_value,
            average_unit_cost
        FROM moving_average_balances
        WHERE company_id = :company_id
          AND product_id = :product_id
          AND warehouse_id = :warehouse_id
        """,
        {
            "company_id":
                COMPANY_ID,
            "product_id":
                product_id,
            "warehouse_id":
                warehouse_id,
        },
    )


async def active_ma_movements(
    db,
    *,
    product_id,
    warehouse_id,
):
    return await rows(
        db,
        """
        SELECT
            m.id,
            m.document_id,
            m.document_line_id,
            m.movement_type,
            m.movement_date,
            m.quantity_delta,
            m.value_delta,
            m.unit_cost,
            m.balance_quantity_after,
            m.balance_value_after,
            m.average_unit_cost_after
        FROM moving_average_movements m
        WHERE m.company_id = :company_id
          AND m.product_id = :product_id
          AND m.warehouse_id = :warehouse_id
          AND NOT EXISTS (
              SELECT 1
              FROM moving_average_movements r
              WHERE r.company_id = m.company_id
                AND r.reversal_of_id = m.id
          )
        ORDER BY m.id
        """,
        {
            "company_id":
                COMPANY_ID,
            "product_id":
                product_id,
            "warehouse_id":
                warehouse_id,
        },
    )


async def inventory_cost_entry(
    db,
    *,
    document_id,
    document_line_id,
):
    return await mapping_one(
        db,
        """
        SELECT
            id,
            company_id,
            document_id,
            document_line_id,
            valuation_method,
            quantity,
            unit_cost,
            valuation_amount,
            cost_amount,
            created_at
        FROM inventory_cost_entries
        WHERE company_id = :company_id
          AND document_id = :document_id
          AND document_line_id = :document_line_id
        """,
        {
            "company_id":
                COMPANY_ID,
            "document_id":
                document_id,
            "document_line_id":
                document_line_id,
        },
    )


def money(
    value,
):
    return Decimal(
        value
    ).quantize(
        Decimal(
            "0.00000001"
        )
    )


from app.services.trade_fulfillment_service import (
    execute_sales_order_fulfillment_reversal,
)


@pytest.mark.asyncio
async def test_purchase_value_correction_moving_average_technical_issue_reversal_postgresql():
    """
    FULL REAL POSTGRESQL PVC MA CHRONOLOGY

    Prove same-receipt peer PVC replay and accounting across a real
    moving-average receipt followed by a real normal ISSUE.

    D1:
        real purchase fulfillment
        -> POSTED RECEIPT
        -> MovingAverageMovement RECEIPT
        -> MovingAverageBalance

    D4:
        real sales fulfillment
        -> POSTED ISSUE
        -> MovingAverageMovement ISSUE
        -> immutable base InventoryCostEntry
        -> normal document accounting

    Invariants:
        base MA movement history is real production history;
        InventoryCostEntry stores base weighted-average ISSUE cost;
        caller transaction owns rollback;
        no committed database changes.

    PVC same-receipt peers are reconciled on D2 while all stock is
    on hand, then production D4 ISSUE migrates the immutable PVC
    topology forward to issued + remaining on_hand effects.
    """

    # Shared async engine pools may contain a connection created by
    # another pytest event loop.
    await engine.dispose(
        close=False
    )

    baseline = (
        await complete_table_counts()
    )

    scenario_error = None
    scenario_traceback = None

    async with engine.connect() as connection:
        transaction = (
            await connection.begin()
        )

        db = AsyncSession(
            bind=connection,
            expire_on_commit=False,
        )

        try:
            fixture = (
                await base.base.create_business_fixture(
                    db
                )
            )

            d1 = fixture[
                "business_date"
            ]

            d4 = (
                d1
                + timedelta(
                    days=3
                )
            )

            # -------------------------------------------------
            # COMPANY VALUATION METHOD — TRANSACTION LOCAL
            # -------------------------------------------------

            await set_company_to_moving_average(
                db
            )

            # -------------------------------------------------
            # D1 REAL PURCHASE RECEIPT
            # -------------------------------------------------

            purchase_result = (
                await base.execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=fixture[
                        "order_id"
                    ],
                    warehouse_document_number=(
                        "PVC-MA-PG-R-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    document_date=d1,
                    accounting_rule_id=fixture[
                        "accounting_rule_id"
                    ],
                    created_by=USER_ID,
                    request_lines=(
                        base.PurchaseOrderFulfillmentRequestLine(
                            trade_document_line_id=fixture[
                                "order_line_id"
                            ],
                            quantity=Decimal(
                                "100.0000"
                            ),
                        ),
                    ),
                )
            )

            await db.flush()

            assert (
                purchase_result.fulfillment.id
                is not None
            )

            receipt_line = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        tfl.id AS fulfillment_line_id,
                        tfl.warehouse_document_id,
                        tfl.warehouse_document_line_id,
                        tfl.product_id,
                        tfl.warehouse_id,
                        tfl.quantity,
                        d.status,
                        d.document_type,
                        d.document_date,
                        dl.price
                    FROM trade_fulfillment_lines tfl
                    JOIN documents d
                      ON d.company_id =
                         tfl.company_id
                     AND d.id =
                         tfl.warehouse_document_id
                    JOIN document_lines dl
                      ON dl.document_id =
                         tfl.warehouse_document_id
                     AND dl.id =
                         tfl.warehouse_document_line_id
                    WHERE tfl.company_id =
                          :company_id
                      AND tfl.fulfillment_id =
                          :fulfillment_id
                    ORDER BY tfl.id
                    LIMIT 1
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "fulfillment_id": (
                            purchase_result
                            .fulfillment
                            .id
                        ),
                    },
                )
            )

            assert (
                receipt_line[
                    "status"
                ]
                == "posted"
            )

            assert (
                receipt_line[
                    "document_type"
                ]
                == "receipt"
            )

            assert (
                receipt_line[
                    "document_date"
                ]
                == d1
            )

            product_id = int(
                receipt_line[
                    "product_id"
                ]
            )

            warehouse_id = int(
                receipt_line[
                    "warehouse_id"
                ]
            )

            receipt_document_id = int(
                receipt_line[
                    "warehouse_document_id"
                ]
            )

            receipt_document_line_id = int(
                receipt_line[
                    "warehouse_document_line_id"
                ]
            )

            receipt_movements = (
                await active_ma_movements(
                    db,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            )

            receipt_rows = tuple(
                row
                for row in receipt_movements
                if (
                    int(
                        row[
                            "document_id"
                        ]
                    )
                    == receipt_document_id
                    and int(
                        row[
                            "document_line_id"
                        ]
                    )
                    == receipt_document_line_id
                )
            )

            assert len(
                receipt_rows
            ) == 1

            receipt_ma = (
                receipt_rows[
                    0
                ]
            )

            assert (
                receipt_ma[
                    "movement_type"
                ]
                == "receipt"
            )

            assert money(
                receipt_ma[
                    "quantity_delta"
                ]
            ) == money(
                "100"
            )

            assert (
                receipt_ma[
                    "movement_date"
                ]
                == d1
            )

            d1_balance = (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            )

            assert money(
                d1_balance[
                    "quantity"
                ]
            ) == money(
                "100"
            )

            assert money(
                d1_balance[
                    "inventory_value"
                ]
            ) == money(
                receipt_ma[
                    "value_delta"
                ]
            )

            assert money(
                d1_balance[
                    "average_unit_cost"
                ]
            ) == money(
                receipt_ma[
                    "unit_cost"
                ]
            )

            receipt_snapshot = dict(
                receipt_ma
            )

            # =================================================
            # D2 — TWO ACTIVE PVC PEERS ON ONE PHYSICAL RECEIPT
            #
            # The physical receipt is immutable:
            #     quantity = 100
            #
            # Two active invoice-fulfillment allocations partition
            # that same receipt:
            #     peer A = 60
            #     peer B = 40
            #
            # One commercial correction 100 -> 90 must allocate:
            #     60 -> 54 = -6
            #     40 -> 36 = -4
            #
            # This is exactly the same-receipt peer topology that
            # the MA peer replay layer is designed to aggregate.
            # =================================================

            d2 = (
                d1
                + timedelta(
                    days=1
                )
            )

            fulfillment_line_id = int(
                receipt_line[
                    "fulfillment_line_id"
                ]
            )

            # Peer A uses the fixture purchase invoice line.
            #
            # That invoice line is economically 120.00. Allocating
            # only 60 units and correcting the line 120 -> 108 gives
            # the required allocated marginal correction:
            #
            #     60 -> 54 = -6
            #
            allocation_a = (
                await create_invoice_fulfillment_allocation(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=fixture[
                        "invoice_id"
                    ],
                    invoice_line_id=fixture[
                        "invoice_line_id"
                    ],
                    fulfillment_id=(
                        purchase_result
                        .fulfillment
                        .id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    quantity=Decimal(
                        "60.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert allocation_a.id is not None

            # -------------------------------------------------
            # SECOND REAL PURCHASE INVOICE LINE
            #
            # Production IFA correctly forbids two ACTIVE
            # allocations for the exact same:
            #
            #     invoice_line + fulfillment_line
            #
            # Therefore peer B must have distinct commercial
            # invoice provenance while still pointing to the
            # SAME physical receipt line.
            #
            # We create a second confirmed purchase invoice with
            # one 40-unit line for the same supplier/product/
            # warehouse. No warehouse history is created here.
            # -------------------------------------------------

            invoice_b_id = await scalar(
                db,
                """
                INSERT INTO trade_documents (
                    company_id,
                    counterparty_id,
                    contract_id,
                    number,
                    direction,
                    kind,
                    status,
                    document_date,
                    currency_code,
                    payment_term_days,
                    created_by,
                    confirmed_at
                )
                SELECT
                    company_id,
                    counterparty_id,
                    contract_id,
                    :number,
                    'purchase',
                    'invoice',
                    'confirmed',
                    :document_date,
                    currency_code,
                    payment_term_days,
                    :created_by,
                    CURRENT_TIMESTAMP
                FROM trade_documents
                WHERE company_id = :company_id
                  AND id = :source_invoice_id
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "source_invoice_id":
                        fixture[
                            "invoice_id"
                        ],
                    "number": (
                        "PI-PVC-MA-B-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    "document_date":
                        d1,
                    "created_by":
                        USER_ID,
                },
            )

            invoice_b_line_id = await scalar(
                db,
                """
                INSERT INTO trade_document_lines (
                    company_id,
                    trade_document_id,
                    line_number,
                    product_id,
                    warehouse_id,
                    quantity,
                    unit_price
                )
                VALUES (
                    :company_id,
                    :trade_document_id,
                    1,
                    :product_id,
                    :warehouse_id,
                    40.0000,
                    1.0000
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "trade_document_id":
                        int(
                            invoice_b_id
                        ),
                    "product_id":
                        product_id,
                    "warehouse_id":
                        warehouse_id,
                },
            )

            await db.flush()

            # A confirmed purchase invoice is not complete commercial
            # provenance for production allocation lifecycle without
            # its PAYABLE open item.
            #
            # create_invoice_fulfillment_allocation() intentionally
            # invokes supplier-advance-clearing reconciliation, which
            # requires the invoice payable to exist.
            invoice_b_open_item_id = await scalar(
                db,
                """
                INSERT INTO counterparty_open_items (
                    company_id,
                    trade_document_id,
                    counterparty_id,
                    contract_id,
                    item_type,
                    status,
                    document_date,
                    due_date,
                    currency_code,
                    original_amount
                )
                SELECT
                    td.company_id,
                    td.id,
                    td.counterparty_id,
                    td.contract_id,
                    'payable',
                    'open',
                    td.document_date,
                    td.document_date
                        + td.payment_term_days,
                    td.currency_code,
                    40.00
                FROM trade_documents td
                WHERE td.company_id = :company_id
                  AND td.id = :invoice_id
                  AND td.direction = 'purchase'
                  AND td.kind = 'invoice'
                  AND td.status = 'confirmed'
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "invoice_id":
                        int(invoice_b_id),
                },
            )

            assert invoice_b_open_item_id is not None

            await db.flush()

            allocation_b = (
                await create_invoice_fulfillment_allocation(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=int(
                        invoice_b_id
                    ),
                    invoice_line_id=int(
                        invoice_b_line_id
                    ),
                    fulfillment_id=(
                        purchase_result
                        .fulfillment
                        .id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    quantity=Decimal(
                        "40.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert allocation_b.id is not None
            assert allocation_a.id != allocation_b.id

            # Both IFA rows must point to one immutable physical
            # warehouse receipt line.
            physical_roots = (
                await rows(
                    db,
                    """
                    SELECT
                        ifa.id,
                        ifa.invoice_id,
                        ifa.invoice_line_id,
                        ifa.fulfillment_id,
                        ifa.fulfillment_line_id,
                        tfl.warehouse_document_id,
                        tfl.warehouse_document_line_id,
                        tfl.product_id,
                        tfl.warehouse_id,
                        ifa.quantity
                    FROM invoice_fulfillment_allocations ifa
                    JOIN trade_fulfillment_lines tfl
                      ON tfl.company_id =
                           ifa.company_id
                       AND tfl.fulfillment_id =
                           ifa.fulfillment_id
                       AND tfl.id =
                           ifa.fulfillment_line_id
                    WHERE ifa.company_id = :company_id
                      AND ifa.id = ANY(:ifa_ids)
                    ORDER BY ifa.id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "ifa_ids": [
                            int(
                                allocation_a.id
                            ),
                            int(
                                allocation_b.id
                            ),
                        ],
                    },
                )
            )

            assert len(
                physical_roots
            ) == 2

            assert {
                int(
                    row[
                        "invoice_line_id"
                    ]
                )
                for row in physical_roots
            } == {
                int(
                    fixture[
                        "invoice_line_id"
                    ]
                ),
                int(
                    invoice_b_line_id
                ),
            }

            assert {
                int(
                    row[
                        "warehouse_document_id"
                    ]
                )
                for row in physical_roots
            } == {
                receipt_document_id
            }

            assert {
                int(
                    row[
                        "warehouse_document_line_id"
                    ]
                )
                for row in physical_roots
            } == {
                receipt_document_line_id
            }

            assert sum(
                (
                    Decimal(
                        row[
                            "quantity"
                        ]
                    )
                    for row in physical_roots
                ),
                Decimal("0"),
            ) == Decimal(
                "100.0000"
            )

            # -------------------------------------------------
            # PEER A COMMERCIAL PVC
            #
            # Invoice A source line = 120.
            # Only 60 is allocated to this physical receipt.
            # 120 -> 108 therefore gives allocated:
            #
            #     60 -> 54
            # -------------------------------------------------

            correction_a = (
                TradeValueCorrectionEvent(
                    company_id=COMPANY_ID,
                    direction="purchase",
                    trade_document_id=fixture[
                        "invoice_id"
                    ],
                    trade_document_line_id=fixture[
                        "invoice_line_id"
                    ],
                    product_id=product_id,
                    correction_date=d2,
                    original_gross_amount=Decimal(
                        "120.00"
                    ),
                    original_tax_amount=Decimal(
                        "0.00"
                    ),
                    corrected_gross_amount=Decimal(
                        "108.00"
                    ),
                    corrected_tax_amount=Decimal(
                        "0.00"
                    ),
                    currency_code="UAH",
                    reason_code=(
                        "postgres_ma_peer_a"
                    ),
                    created_by=USER_ID,
                    reversal_of_id=None,
                )
            )

            db.add(
                correction_a
            )
            await db.flush()

            allocation_result_a = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction_a.id
                    ),
                    adjustment_date=d2,
                    created_by=USER_ID,
                )
            )

            assert (
                allocation_result_a
                .source_is_active
                is True
            )

            assert len(
                allocation_result_a
                .created_events
            ) == 1

            pvc_a = (
                allocation_result_a
                .created_events[
                    0
                ]
            )

            assert (
                pvc_a
                .invoice_fulfillment_allocation_id
                == allocation_a.id
            )

            assert money(
                pvc_a.original_allocated_base_amount
            ) == money(
                "60"
            )

            assert money(
                pvc_a.corrected_allocated_base_amount
            ) == money(
                "54"
            )

            assert (
                pvc_a.recognition_date
                == d2
            )

            # -------------------------------------------------
            # PEER B COMMERCIAL PVC
            #
            # Independent invoice line:
            #
            #     40 -> 36
            #
            # It still belongs to the same physical receipt root.
            # -------------------------------------------------

            correction_b = (
                TradeValueCorrectionEvent(
                    company_id=COMPANY_ID,
                    direction="purchase",
                    trade_document_id=int(
                        invoice_b_id
                    ),
                    trade_document_line_id=int(
                        invoice_b_line_id
                    ),
                    product_id=product_id,
                    correction_date=d2,
                    original_gross_amount=Decimal(
                        "40.00"
                    ),
                    original_tax_amount=Decimal(
                        "0.00"
                    ),
                    corrected_gross_amount=Decimal(
                        "36.00"
                    ),
                    corrected_tax_amount=Decimal(
                        "0.00"
                    ),
                    currency_code="UAH",
                    reason_code=(
                        "postgres_ma_peer_b"
                    ),
                    created_by=USER_ID,
                    reversal_of_id=None,
                )
            )

            db.add(
                correction_b
            )
            await db.flush()

            allocation_result_b = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction_b.id
                    ),
                    adjustment_date=d2,
                    created_by=USER_ID,
                )
            )

            assert (
                allocation_result_b
                .source_is_active
                is True
            )

            assert len(
                allocation_result_b
                .created_events
            ) == 1

            pvc_b = (
                allocation_result_b
                .created_events[
                    0
                ]
            )

            assert (
                pvc_b
                .invoice_fulfillment_allocation_id
                == allocation_b.id
            )

            assert money(
                pvc_b.original_allocated_base_amount
            ) == money(
                "40"
            )

            assert money(
                pvc_b.corrected_allocated_base_amount
            ) == money(
                "36"
            )

            assert (
                pvc_b.recognition_date
                == d2
            )

            # Exact peer conservation before MA replay.
            assert (
                money(
                    Decimal(
                        pvc_a.corrected_allocated_base_amount
                    )
                    - Decimal(
                        pvc_a.original_allocated_base_amount
                    )
                )
                == money(
                    "-6"
                )
            )

            assert (
                money(
                    Decimal(
                        pvc_b.corrected_allocated_base_amount
                    )
                    - Decimal(
                        pvc_b.original_allocated_base_amount
                    )
                )
                == money(
                    "-4"
                )
            )

            anchor_allocation_event_id = min(
                int(
                    pvc_a.id
                ),
                int(
                    pvc_b.id
                ),
            )

            # -------------------------------------------------
            # D2 INITIAL PEER REPLAY + GL
            #
            # Before any ISSUE exists, the complete -10 belongs
            # to on_hand.
            # -------------------------------------------------

            d2_reconciliation = (
                await reconcile_and_post_purchase_value_correction_moving_average_peers(
                    db,
                    company_id=COMPANY_ID,
                    anchor_allocation_event_id=(
                        anchor_allocation_event_id
                    ),
                    adjustment_date=d2,
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert len(
                d2_reconciliation.created_events
            ) == 2

            d2_events = tuple(
                sorted(
                    d2_reconciliation.created_events,
                    key=lambda event: (
                        event
                        .purchase_value_correction_allocation_event_id
                    ),
                )
            )

            assert {
                event.effect_kind
                for event in d2_events
            } == {
                "on_hand"
            }

            assert {
                int(
                    event
                    .purchase_value_correction_allocation_event_id
                )
                for event in d2_events
            } == {
                int(
                    pvc_a.id
                ),
                int(
                    pvc_b.id
                ),
            }

            d2_delta_by_allocation = {
                int(
                    event
                    .purchase_value_correction_allocation_event_id
                ): money(
                    Decimal(
                        event.corrected_valuation_amount
                    )
                    - Decimal(
                        event.original_valuation_amount
                    )
                )
                for event in d2_events
            }

            assert d2_delta_by_allocation[
                int(
                    pvc_a.id
                )
            ] == money(
                "-6"
            )

            assert d2_delta_by_allocation[
                int(
                    pvc_b.id
                )
            ] == money(
                "-4"
            )

            assert sum(
                d2_delta_by_allocation.values(),
                ZERO,
            ) == money(
                "-10"
            )

            for event in d2_events:
                assert (
                    event.recognition_date
                    == d2
                )
                assert (
                    event.source_moving_average_movement_id
                    is None
                )
                assert (
                    event.source_inventory_cost_entry_id
                    is None
                )

            d2_event_ids = tuple(
                int(
                    event.id
                )
                for event in d2_events
            )

            d2_typed_journals = (
                await rows(
                    db,
                    """
                    SELECT
                        je.id,
                        je.entry_date,
                        je.status,
                        je.reversal_of_id,
                        je.purchase_value_correction_ma_replay_event_id
                    FROM journal_entries je
                    WHERE je.company_id = :company_id
                      AND je.purchase_value_correction_ma_replay_event_id
                          = ANY(:event_ids)
                    ORDER BY je.id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "event_ids":
                            list(
                                d2_event_ids
                            ),
                    },
                )
            )

            assert len(
                d2_typed_journals
            ) == 2

            assert {
                int(
                    row[
                        "purchase_value_correction_ma_replay_event_id"
                    ]
                )
                for row in d2_typed_journals
            } == set(
                d2_event_ids
            )

            assert all(
                row[
                    "entry_date"
                ]
                == d2
                for row in d2_typed_journals
            )

            d2_event_snapshot = {
                int(
                    event.id
                ): {
                    "allocation_id": int(
                        event
                        .purchase_value_correction_allocation_event_id
                    ),
                    "effect_kind":
                        event.effect_kind,
                    "recognition_date":
                        event.recognition_date,
                    "quantity":
                        Decimal(
                            event.quantity
                        ),
                    "original":
                        Decimal(
                            event.original_valuation_amount
                        ),
                    "corrected":
                        Decimal(
                            event.corrected_valuation_amount
                        ),
                    "reversal_of_id":
                        event.reversal_of_id,
                }
                for event in d2_events
            }

            # Base warehouse state is NOT rewritten by PVC.
            d2_balance = (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            )

            assert money(
                d2_balance[
                    "quantity"
                ]
            ) == money(
                "100"
            )

            assert money(
                d2_balance[
                    "inventory_value"
                ]
            ) == money(
                receipt_snapshot[
                    "balance_value_after"
                ]
            )

            receipt_after_d2 = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        document_id,
                        document_line_id,
                        movement_type,
                        movement_date,
                        quantity_delta,
                        value_delta,
                        unit_cost,
                        balance_quantity_after,
                        balance_value_after,
                        average_unit_cost_after
                    FROM moving_average_movements
                    WHERE id = :movement_id
                    """,
                    {
                        "movement_id":
                            receipt_snapshot[
                                "id"
                            ],
                    },
                )
            )

            for key in (
                "id",
                "document_id",
                "document_line_id",
                "movement_type",
                "movement_date",
                "quantity_delta",
                "value_delta",
                "unit_cost",
                "balance_quantity_after",
                "balance_value_after",
                "average_unit_cost_after",
            ):
                assert (
                    receipt_after_d2[
                        key
                    ]
                    == receipt_snapshot[
                        key
                    ]
                )

            print(
                "D2 TWO SAME-RECEIPT PVC PEERS = PASS"
            )
            print(
                "D2 MARGINAL on_hand REPLAY = PASS"
            )
            print(
                "D2 TYPED PVC MA GL = PASS"
            )

            # =================================================
            # D4 REAL NORMAL MA ISSUE
            #
            # Production order must now be:
            #
            # warehouse:
            #   base MA ISSUE
            #   immutable base ICE
            #   peer replay reconciliation
            #
            # accounting:
            #   normal ISSUE JE
            #
            # post-accounting:
            #   PVC MA reversal/replacement JEs
            # =================================================

            await base.find_or_seed_issue_accounting_rule(
                db
            )

            (
                sales_order_id,
                sales_order_line_id,
            ) = (
                await base.create_sales_order(
                    db,
                    fixture=fixture,
                    quantity=Decimal(
                        "40.0000"
                    ),
                )
            )

            await db.flush()

            await reserve_source_line(
                db,
                company_id=COMPANY_ID,
                source_document_id=sales_order_id,
                source_document_line_id=(
                    sales_order_line_id
                ),
                quantity=Decimal(
                    "40.0000"
                ),
            )

            await db.flush()

            issue_rule_id = (
                await base.find_or_seed_issue_accounting_rule(
                    db
                )
            )

            journals_before_issue = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM journal_entries
                WHERE company_id = :company_id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                },
            )

            sales_result = (
                await execute_sales_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=sales_order_id,
                    warehouse_document_number=(
                        "PVC-MA-PG-I-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    document_date=d4,
                    accounting_rule_id=issue_rule_id,
                    created_by=USER_ID,
                    request_lines=(
                        SalesOrderFulfillmentRequestLine(
                            trade_document_line_id=(
                                sales_order_line_id
                            ),
                            quantity=Decimal(
                                "40.0000"
                            ),
                        ),
                    ),
                )
            )

            await db.flush()

            issue_line = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        tfl.id AS fulfillment_line_id,
                        tfl.warehouse_document_id,
                        tfl.warehouse_document_line_id,
                        tfl.product_id,
                        tfl.warehouse_id,
                        tfl.quantity,
                        d.status,
                        d.document_type,
                        d.document_date
                    FROM trade_fulfillment_lines tfl
                    JOIN documents d
                      ON d.company_id =
                           tfl.company_id
                       AND d.id =
                           tfl.warehouse_document_id
                    WHERE tfl.company_id =
                          :company_id
                      AND tfl.fulfillment_id =
                          :fulfillment_id
                    ORDER BY tfl.id
                    LIMIT 1
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "fulfillment_id": (
                            sales_result
                            .fulfillment
                            .id
                        ),
                    },
                )
            )

            assert (
                issue_line[
                    "status"
                ]
                == "posted"
            )

            assert (
                issue_line[
                    "document_type"
                ]
                == "issue"
            )

            assert (
                issue_line[
                    "document_date"
                ]
                == d4
            )

            issue_document_id = int(
                issue_line[
                    "warehouse_document_id"
                ]
            )

            issue_document_line_id = int(
                issue_line[
                    "warehouse_document_line_id"
                ]
            )

            all_movements = (
                await active_ma_movements(
                    db,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            )

            issue_rows = tuple(
                row
                for row in all_movements
                if (
                    int(
                        row[
                            "document_id"
                        ]
                    )
                    == issue_document_id
                    and int(
                        row[
                            "document_line_id"
                        ]
                    )
                    == issue_document_line_id
                )
            )

            assert len(
                issue_rows
            ) == 1

            issue_ma = (
                issue_rows[
                    0
                ]
            )

            assert (
                issue_ma[
                    "movement_type"
                ]
                == "issue"
            )

            assert money(
                issue_ma[
                    "quantity_delta"
                ]
            ) == money(
                "-40"
            )

            assert (
                issue_ma[
                    "movement_date"
                ]
                == d4
            )

            ice = (
                await inventory_cost_entry(
                    db,
                    document_id=issue_document_id,
                    document_line_id=issue_document_line_id,
                )
            )

            assert (
                ice[
                    "valuation_method"
                ]
                == "weighted_average_moving"
            )

            assert money(
                ice[
                    "quantity"
                ]
            ) == money(
                "40"
            )

            assert money(
                ice[
                    "valuation_amount"
                ]
            ) == money(
                -Decimal(
                    issue_ma[
                        "value_delta"
                    ]
                )
            )

            assert money(
                ice[
                    "unit_cost"
                ]
            ) == money(
                issue_ma[
                    "unit_cost"
                ]
            )

            issue_ma_snapshot = dict(
                issue_ma
            )
            ice_snapshot = dict(
                ice
            )

            d4_balance = (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            )

            assert money(
                d4_balance[
                    "quantity"
                ]
            ) == money(
                "60"
            )

            assert money(
                d4_balance[
                    "inventory_value"
                ]
            ) == money(
                issue_ma[
                    "balance_value_after"
                ]
            )

            # -------------------------------------------------
            # D4 REPLAY EVENT HISTORY
            #
            # Each D2 on_hand original must now have one D4
            # reversal. Replacement topology must contain issued
            # and remaining on_hand marginal effects.
            # -------------------------------------------------

            replay_history = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        purchase_value_correction_allocation_event_id,
                        product_id,
                        warehouse_id,
                        effect_kind,
                        source_moving_average_movement_id,
                        source_inventory_cost_entry_id,
                        recognition_date,
                        quantity,
                        original_valuation_amount,
                        corrected_valuation_amount,
                        reversal_of_id
                    FROM purchase_value_correction_ma_replay_events
                    WHERE company_id = :company_id
                      AND product_id = :product_id
                      AND warehouse_id = :warehouse_id
                    ORDER BY id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "product_id":
                            product_id,
                        "warehouse_id":
                            warehouse_id,
                    },
                )
            )

            reversed_ids = {
                int(
                    row[
                        "reversal_of_id"
                    ]
                )
                for row in replay_history
                if (
                    row[
                        "reversal_of_id"
                    ]
                    is not None
                )
            }

            for event_id in d2_event_ids:
                assert (
                    event_id
                    in reversed_ids
                )

            d4_reversals = tuple(
                row
                for row in replay_history
                if (
                    row[
                        "reversal_of_id"
                    ]
                    in d2_event_ids
                )
            )

            assert len(
                d4_reversals
            ) == 2

            assert all(
                row[
                    "recognition_date"
                ]
                == d4
                for row in d4_reversals
            )

            for reversal in d4_reversals:
                original = d2_event_snapshot[
                    int(
                        reversal[
                            "reversal_of_id"
                        ]
                    )
                ]

                assert (
                    reversal[
                        "effect_kind"
                    ]
                    == original[
                        "effect_kind"
                    ]
                )

                assert money(
                    reversal[
                        "quantity"
                    ]
                ) == money(
                    original[
                        "quantity"
                    ]
                )

                assert money(
                    reversal[
                        "original_valuation_amount"
                    ]
                ) == money(
                    original[
                        "corrected"
                    ]
                )

                assert money(
                    reversal[
                        "corrected_valuation_amount"
                    ]
                ) == money(
                    original[
                        "original"
                    ]
                )

            active_replay = tuple(
                row
                for row in replay_history
                if (
                    row[
                        "reversal_of_id"
                    ]
                    is None
                    and int(
                        row[
                            "id"
                        ]
                    )
                    not in reversed_ids
                )
            )

            assert len(
                active_replay
            ) == 4

            active_issued = tuple(
                row
                for row in active_replay
                if (
                    row[
                        "effect_kind"
                    ]
                    == "issued"
                )
            )

            active_on_hand = tuple(
                row
                for row in active_replay
                if (
                    row[
                        "effect_kind"
                    ]
                    == "on_hand"
                )
            )

            assert len(
                active_issued
            ) == 2

            assert len(
                active_on_hand
            ) == 2

            assert all(
                row[
                    "recognition_date"
                ]
                == d4
                for row in active_issued
            )

            assert all(
                row[
                    "recognition_date"
                ]
                == d4
                for row in active_on_hand
            )

            assert all(
                int(
                    row[
                        "source_moving_average_movement_id"
                    ]
                )
                == int(
                    issue_ma[
                        "id"
                    ]
                )
                for row in active_issued
            )

            assert all(
                int(
                    row[
                        "source_inventory_cost_entry_id"
                    ]
                )
                == int(
                    ice[
                        "id"
                    ]
                )
                for row in active_issued
            )

            assert all(
                row[
                    "source_moving_average_movement_id"
                ]
                is None
                for row in active_on_hand
            )

            assert all(
                row[
                    "source_inventory_cost_entry_id"
                ]
                is None
                for row in active_on_hand
            )

            issued_delta = sum(
                (
                    money(
                        Decimal(
                            row[
                                "corrected_valuation_amount"
                            ]
                        )
                        - Decimal(
                            row[
                                "original_valuation_amount"
                            ]
                        )
                    )
                    for row in active_issued
                ),
                ZERO,
            )

            on_hand_delta = sum(
                (
                    money(
                        Decimal(
                            row[
                                "corrected_valuation_amount"
                            ]
                        )
                        - Decimal(
                            row[
                                "original_valuation_amount"
                            ]
                        )
                    )
                    for row in active_on_hand
                ),
                ZERO,
            )

            assert issued_delta == money(
                "-4"
            )

            assert on_hand_delta == money(
                "-6"
            )

            assert (
                issued_delta
                + on_hand_delta
            ) == money(
                "-10"
            )

            # -------------------------------------------------
            # NORMAL ISSUE ACCOUNTING EXISTS
            # -------------------------------------------------

            normal_issue_journal = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        entry_date,
                        accounting_rule_id
                    FROM journal_entries
                    WHERE company_id = :company_id
                      AND document_id = :document_id
                      AND purchase_value_correction_ma_replay_event_id
                          IS NULL
                    ORDER BY id
                    LIMIT 1
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "document_id":
                            issue_document_id,
                    },
                )
            )

            assert (
                normal_issue_journal[
                    "entry_date"
                ]
                == d4
            )

            normal_issue_debit = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        account_id,
                        debit,
                        credit
                    FROM journal_entry_lines
                    WHERE journal_entry_id = :journal_entry_id
                      AND debit > 0
                    ORDER BY line_no
                    LIMIT 1
                    """,
                    {
                        "journal_entry_id":
                            int(
                                normal_issue_journal[
                                    "id"
                                ]
                            ),
                    },
                )
            )

            historical_issue_destination_account_id = int(
                normal_issue_debit[
                    "account_id"
                ]
            )

            # -------------------------------------------------
            # EVERY PVC MA EVENT HAS EXACTLY ONE TYPED JE
            # -------------------------------------------------

            replay_ids = tuple(
                int(
                    row[
                        "id"
                    ]
                )
                for row in replay_history
            )

            typed_journals = (
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        entry_date,
                        status,
                        reversal_of_id,
                        purchase_value_correction_ma_replay_event_id
                    FROM journal_entries
                    WHERE company_id = :company_id
                      AND purchase_value_correction_ma_replay_event_id
                          = ANY(:event_ids)
                    ORDER BY id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "event_ids":
                            list(
                                replay_ids
                            ),
                    },
                )
            )

            assert len(
                typed_journals
            ) == len(
                replay_ids
            )

            typed_by_event = {
                int(
                    row[
                        "purchase_value_correction_ma_replay_event_id"
                    ]
                ): row
                for row in typed_journals
            }

            assert set(
                typed_by_event
            ) == set(
                replay_ids
            )

            replay_by_id = {
                int(
                    row[
                        "id"
                    ]
                ): row
                for row in replay_history
            }

            for event_id, journal in typed_by_event.items():
                assert (
                    journal[
                        "entry_date"
                    ]
                    == replay_by_id[
                        event_id
                    ][
                        "recognition_date"
                    ]
                )

            # -------------------------------------------------
            # REVERSAL JOURNALS EXACTLY MIRROR D2 ORIGINALS
            # -------------------------------------------------

            for reversal in d4_reversals:
                reversal_event_id = int(
                    reversal[
                        "id"
                    ]
                )

                original_event_id = int(
                    reversal[
                        "reversal_of_id"
                    ]
                )

                reversal_je = typed_by_event[
                    reversal_event_id
                ]

                original_je = typed_by_event[
                    original_event_id
                ]

                assert int(
                    reversal_je[
                        "reversal_of_id"
                    ]
                ) == int(
                    original_je[
                        "id"
                    ]
                )

                original_lines = (
                    await rows(
                        db,
                        """
                        SELECT
                            account_id,
                            debit,
                            credit
                        FROM journal_entry_lines
                        WHERE journal_entry_id = :journal_entry_id
                        ORDER BY line_no
                        """,
                        {
                            "journal_entry_id":
                                int(
                                    original_je[
                                        "id"
                                    ]
                                ),
                        },
                    )
                )

                reversal_lines = (
                    await rows(
                        db,
                        """
                        SELECT
                            account_id,
                            debit,
                            credit
                        FROM journal_entry_lines
                        WHERE journal_entry_id = :journal_entry_id
                        ORDER BY line_no
                        """,
                        {
                            "journal_entry_id":
                                int(
                                    reversal_je[
                                        "id"
                                    ]
                                ),
                        },
                    )
                )

                assert len(
                    original_lines
                ) == len(
                    reversal_lines
                )

                for original_line, reversal_line in zip(
                    original_lines,
                    reversal_lines,
                    strict=True,
                ):
                    assert int(
                        original_line[
                            "account_id"
                        ]
                    ) == int(
                        reversal_line[
                            "account_id"
                        ]
                    )

                    assert money(
                        original_line[
                            "debit"
                        ]
                    ) == money(
                        reversal_line[
                            "credit"
                        ]
                    )

                    assert money(
                        original_line[
                            "credit"
                        ]
                    ) == money(
                        reversal_line[
                            "debit"
                        ]
                    )

            # -------------------------------------------------
            # ISSUED PVC DESTINATION = HISTORICAL NORMAL ISSUE
            # DESTINATION, NEVER CURRENT ROLE / HARDCODED 902.
            # -------------------------------------------------

            for issued_event in active_issued:
                issued_je = typed_by_event[
                    int(
                        issued_event[
                            "id"
                        ]
                    )
                ]

                issued_lines = (
                    await rows(
                        db,
                        """
                        SELECT
                            account_id,
                            debit,
                            credit
                        FROM journal_entry_lines
                        WHERE journal_entry_id = :journal_entry_id
                        ORDER BY line_no
                        """,
                        {
                            "journal_entry_id":
                                int(
                                    issued_je[
                                        "id"
                                    ]
                                ),
                        },
                    )
                )

                delta = money(
                    Decimal(
                        issued_event[
                            "corrected_valuation_amount"
                        ]
                    )
                    - Decimal(
                        issued_event[
                            "original_valuation_amount"
                        ]
                    )
                )

                assert delta < ZERO

                destination_credit_lines = tuple(
                    line
                    for line in issued_lines
                    if (
                        int(
                            line[
                                "account_id"
                            ]
                        )
                        == historical_issue_destination_account_id
                        and money(
                            line[
                                "credit"
                            ]
                        )
                        == money(
                            -delta
                        )
                    )
                )

                assert len(
                    destination_credit_lines
                ) == 1

            # -------------------------------------------------
            # ORDERING PROOF
            #
            # The normal ISSUE JE must have a lower database id
            # than all D4 PVC MA typed journals. This proves the
            # resolver had historical ISSUE accounting available
            # before PVC GL was posted.
            # -------------------------------------------------

            d4_pvc_journal_ids = tuple(
                int(
                    typed_by_event[
                        int(
                            row[
                                "id"
                            ]
                        )
                    ][
                        "id"
                    ]
                )
                for row in replay_history
                if (
                    row[
                        "recognition_date"
                    ]
                    == d4
                )
            )

            assert d4_pvc_journal_ids

            assert int(
                normal_issue_journal[
                    "id"
                ]
            ) < min(
                d4_pvc_journal_ids
            )

            journals_after_issue = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM journal_entries
                WHERE company_id = :company_id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                },
            )

            assert (
                journals_after_issue
                > journals_before_issue
            )

            # -------------------------------------------------
            # BASE MA + ICE HISTORY REMAINS IMMUTABLE
            # -------------------------------------------------

            receipt_after_issue = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        document_id,
                        document_line_id,
                        movement_type,
                        movement_date,
                        quantity_delta,
                        value_delta,
                        unit_cost,
                        balance_quantity_after,
                        balance_value_after,
                        average_unit_cost_after
                    FROM moving_average_movements
                    WHERE id = :movement_id
                    """,
                    {
                        "movement_id":
                            receipt_snapshot[
                                "id"
                            ],
                    },
                )
            )

            for key in (
                "id",
                "document_id",
                "document_line_id",
                "movement_type",
                "movement_date",
                "quantity_delta",
                "value_delta",
                "unit_cost",
                "balance_quantity_after",
                "balance_value_after",
                "average_unit_cost_after",
            ):
                assert (
                    receipt_after_issue[
                        key
                    ]
                    == receipt_snapshot[
                        key
                    ]
                )

            issue_after_pvc = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        document_id,
                        document_line_id,
                        movement_type,
                        movement_date,
                        quantity_delta,
                        value_delta,
                        unit_cost,
                        balance_quantity_after,
                        balance_value_after,
                        average_unit_cost_after
                    FROM moving_average_movements
                    WHERE id = :movement_id
                    """,
                    {
                        "movement_id":
                            issue_ma_snapshot[
                                "id"
                            ],
                    },
                )
            )

            for key in (
                "id",
                "document_id",
                "document_line_id",
                "movement_type",
                "movement_date",
                "quantity_delta",
                "value_delta",
                "unit_cost",
                "balance_quantity_after",
                "balance_value_after",
                "average_unit_cost_after",
            ):
                assert (
                    issue_after_pvc[
                        key
                    ]
                    == issue_ma_snapshot[
                        key
                    ]
                )

            ice_after_pvc = (
                await inventory_cost_entry(
                    db,
                    document_id=issue_document_id,
                    document_line_id=issue_document_line_id,
                )
            )

            for key in (
                "id",
                "company_id",
                "document_id",
                "document_line_id",
                "valuation_method",
                "quantity",
                "unit_cost",
                "valuation_amount",
                "cost_amount",
                "created_at",
            ):
                assert (
                    ice_after_pvc[
                        key
                    ]
                    == ice_snapshot[
                        key
                    ]
                )
            # SECOND RECONCILIATION = FULL NOOP
            # -------------------------------------------------

            replay_count_before_noop = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM purchase_value_correction_ma_replay_events
                WHERE company_id = :company_id
                  AND product_id = :product_id
                  AND warehouse_id = :warehouse_id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "product_id":
                        product_id,
                    "warehouse_id":
                        warehouse_id,
                },
            )

            pvc_je_count_before_noop = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM journal_entries
                WHERE company_id = :company_id
                  AND purchase_value_correction_ma_replay_event_id
                      IS NOT NULL
                """,
                {
                    "company_id":
                        COMPANY_ID,
                },
            )

            noop = (
                await reconcile_purchase_value_correction_moving_average_peers(
                    db,
                    company_id=COMPANY_ID,
                    anchor_allocation_event_id=(
                        anchor_allocation_event_id
                    ),
                    adjustment_date=d4,
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert (
                noop.created_events
                == ()
            )

            replay_count_after_noop = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM purchase_value_correction_ma_replay_events
                WHERE company_id = :company_id
                  AND product_id = :product_id
                  AND warehouse_id = :warehouse_id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "product_id":
                        product_id,
                    "warehouse_id":
                        warehouse_id,
                },
            )

            pvc_je_count_after_noop = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM journal_entries
                WHERE company_id = :company_id
                  AND purchase_value_correction_ma_replay_event_id
                      IS NOT NULL
                """,
                {
                    "company_id":
                        COMPANY_ID,
                },
            )

            assert (
                replay_count_after_noop
                == replay_count_before_noop
            )

            assert (
                pvc_je_count_after_noop
                == pvc_je_count_before_noop
            )

            print(
                "D1 REAL MA RECEIPT = PASS"
            )
            print(
                "D2 TWO PVC PEERS = PASS"
            )
            print(
                "D2 on_hand PVC MA GL = PASS"
            )
            print(
                "D4 REAL NORMAL MA ISSUE = PASS"
            )
            print(
                "D4 NORMAL ISSUE JE BEFORE PVC GL = PASS"
            )
            print(
                "D4 FORWARD-ONLY PVC REVERSALS = PASS"
            )
            print(
                "D4 issued + on_hand REPLACEMENTS = PASS"
            )
            print(
                "HISTORICAL ISSUE DESTINATION = PASS"
            )
            print(
                "PVC MA TYPED JOURNAL SOURCES = PASS"
            )
            print(
                "REVERSAL JOURNALS EXACT MIRROR = PASS"
            )
            print(
                "BASE MA HISTORY IMMUTABLE = PASS"
            )
            print(
                "BASE INVENTORY COST ENTRY IMMUTABLE = PASS"
            )
            print(
                "SECOND D4 RECONCILE FULL NOOP = PASS"
            )


            # =================================================
            # D6 REAL SALES RETURN PVC MA
            #
            # D1 base receipt:
            #     100 @ 1.00 = 100
            #
            # D2 PVC:
            #     100 -> 90
            #
            # D4 real WAM ISSUE:
            #     base 40
            #     corrected 36
            #     issued PVC delta = -4
            #     on_hand PVC delta = -6
            #
            # D6 real Sales Return 15:
            #     base historical return = 15
            #     corrected economic return = 13.5
            #
            # PVC topology must migrate 15/40 of the existing
            # issued correction:
            #
            #     issued residual = -2.5
            #     on_hand total    = -7.5
            #
            # Base WAM history remains historical/base cost.
            # PVC correction remains dedicated immutable replay.
            # =================================================


            # =================================================
            # D5 REAL TECHNICAL ISSUE REVERSAL
            #
            # D1 receipt:
            #     100 @ 1 = 100
            #
            # D2 aggregate PVC:
            #     100 -> 90
            #     total correction = -10
            #
            # D4 ISSUE 40:
            #     issued PVC = -4
            #     on_hand PVC = -6
            #
            # D5 technical reversal of the exact D4 fulfillment:
            #     base MA ISSUE becomes inactive through immutable
            #     MA REVERSAL history;
            #     issued PVC disappears;
            #     on_hand PVC becomes -10;
            #     total PVC remains -10.
            # =================================================

            d5 = (
                d4
                + timedelta(
                    days=1
                )
            )

            replay_count_before_d5 = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM purchase_value_correction_ma_replay_events
                WHERE company_id = :company_id
                  AND product_id = :product_id
                  AND warehouse_id = :warehouse_id
                """,
                {
                    "company_id": COMPANY_ID,
                    "product_id": product_id,
                    "warehouse_id": warehouse_id,
                },
            )

            pvc_je_count_before_d5 = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM journal_entries
                WHERE company_id = :company_id
                  AND purchase_value_correction_ma_replay_event_id
                      IS NOT NULL
                """,
                {
                    "company_id": COMPANY_ID,
                },
            )

            normal_je_count_before_d5 = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM journal_entries
                WHERE company_id = :company_id
                  AND purchase_value_correction_ma_replay_event_id
                      IS NULL
                """,
                {
                    "company_id": COMPANY_ID,
                },
            )

            reversal = (
                await execute_sales_order_fulfillment_reversal(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=sales_order_id,
                    fulfillment_id=(
                        sales_result
                        .fulfillment
                        .id
                    ),
                    reversal_date=d5,
                    reversed_by=USER_ID,
                )
            )

            await db.flush()

            assert str(
                reversal
                .warehouse_document
                .status
            ) in {
                "reversed",
                "DocumentStatus.REVERSED",
            }

            # -------------------------------------------------
            # BASE MOVING AVERAGE RESTORED
            # -------------------------------------------------

            d5_balance = (
                await ma_balance(
                    db,
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                )
            )

            assert money(
                d5_balance[
                    "quantity"
                ]
            ) == money(
                "100"
            )

            assert money(
                d5_balance[
                    "inventory_value"
                ]
            ) == money(
                "100"
            )

            assert money(
                d5_balance[
                    "average_unit_cost"
                ]
            ) == money(
                "1"
            )

            # Original D4 ISSUE is immutable.
            issue_after_reversal = (
                await mapping_one(
                    db,
                    """
                    SELECT
                        id,
                        document_id,
                        document_line_id,
                        movement_type,
                        movement_date,
                        quantity_delta,
                        value_delta,
                        unit_cost,
                        balance_quantity_after,
                        balance_value_after,
                        average_unit_cost_after
                    FROM moving_average_movements
                    WHERE id = :movement_id
                    """,
                    {
                        "movement_id":
                            issue_ma_snapshot[
                                "id"
                            ],
                    },
                )
            )

            for key in (
                "id",
                "document_id",
                "document_line_id",
                "movement_type",
                "movement_date",
                "quantity_delta",
                "value_delta",
                "unit_cost",
                "balance_quantity_after",
                "balance_value_after",
                "average_unit_cost_after",
            ):
                assert (
                    issue_after_reversal[
                        key
                    ]
                    == issue_ma_snapshot[
                        key
                    ]
                )

            ma_reversal_rows = tuple(
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        document_id,
                        document_line_id,
                        movement_type,
                        movement_date,
                        quantity_delta,
                        value_delta,
                        unit_cost,
                        balance_quantity_after,
                        balance_value_after,
                        average_unit_cost_after,
                        reversal_of_id
                    FROM moving_average_movements
                    WHERE company_id = :company_id
                      AND reversal_of_id = :issue_id
                    ORDER BY id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "issue_id": int(
                            issue_ma_snapshot[
                                "id"
                            ]
                        ),
                    },
                )
            )

            assert len(
                ma_reversal_rows
            ) == 1

            ma_reversal = (
                ma_reversal_rows[
                    0
                ]
            )

            assert str(
                ma_reversal[
                    "movement_type"
                ]
            ).lower().endswith(
                "reversal"
            )

            assert (
                ma_reversal[
                    "movement_date"
                ]
                == d5
            )

            assert money(
                ma_reversal[
                    "quantity_delta"
                ]
            ) == money(
                "40"
            )

            assert money(
                ma_reversal[
                    "value_delta"
                ]
            ) == money(
                "40"
            )

            assert int(
                ma_reversal[
                    "reversal_of_id"
                ]
            ) == int(
                issue_ma_snapshot[
                    "id"
                ]
            )

            # -------------------------------------------------
            # INVENTORY COST ENTRY REMAINS IMMUTABLE
            # -------------------------------------------------

            ice_after_reversal = (
                await inventory_cost_entry(
                    db,
                    document_id=issue_document_id,
                    document_line_id=issue_document_line_id,
                )
            )

            for key in (
                "id",
                "company_id",
                "document_id",
                "document_line_id",
                "valuation_method",
                "quantity",
                "unit_cost",
                "valuation_amount",
                "cost_amount",
                "created_at",
            ):
                assert (
                    ice_after_reversal[
                        key
                    ]
                    == ice_snapshot[
                        key
                    ]
                )

            # -------------------------------------------------
            # ACTIVE PVC TOPOLOGY:
            #     issued = 0
            #     on_hand = -10
            # -------------------------------------------------

            d5_replay = tuple(
                await rows(
                    db,
                    """
                    WITH reversed_ids AS (
                        SELECT DISTINCT reversal_of_id
                        FROM purchase_value_correction_ma_replay_events
                        WHERE company_id = :company_id
                          AND reversal_of_id IS NOT NULL
                    )
                    SELECT
                        e.id,
                        e.purchase_value_correction_allocation_event_id,
                        e.effect_kind,
                        e.source_moving_average_movement_id,
                        e.source_inventory_cost_entry_id,
                        e.recognition_date,
                        e.quantity,
                        e.original_valuation_amount,
                        e.corrected_valuation_amount,
                        e.reversal_of_id
                    FROM purchase_value_correction_ma_replay_events e
                    LEFT JOIN reversed_ids r
                      ON r.reversal_of_id = e.id
                    WHERE e.company_id = :company_id
                      AND e.product_id = :product_id
                      AND e.warehouse_id = :warehouse_id
                      AND e.reversal_of_id IS NULL
                      AND r.reversal_of_id IS NULL
                    ORDER BY e.id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "product_id": product_id,
                        "warehouse_id": warehouse_id,
                    },
                )
            )

            active_issued = tuple(
                row
                for row in d5_replay
                if row[
                    "effect_kind"
                ] == "issued"
            )

            active_on_hand = tuple(
                row
                for row in d5_replay
                if row[
                    "effect_kind"
                ] == "on_hand"
            )

            assert active_issued == ()

            assert active_on_hand

            # Peer-aware replay rows are marginal valuation
            # layers over the SAME physical receipt scope.
            #
            # Therefore peer quantities are NOT additive:
            #
            #     peer A: quantity 100, value 100 -> 94
            #     peer B: quantity 100, value  94 -> 90
            #
            # Physical stock scope remains 100, while valuation
            # deltas are additive: -6 + -4 = -10.
            active_on_hand_quantities = {
                money(
                    row[
                        "quantity"
                    ]
                )
                for row in active_on_hand
            }

            active_on_hand_delta = sum(
                (
                    money(
                        Decimal(
                            row[
                                "corrected_valuation_amount"
                            ]
                        )
                        - Decimal(
                            row[
                                "original_valuation_amount"
                            ]
                        )
                    )
                    for row in active_on_hand
                ),
                ZERO,
            )

            assert (
                active_on_hand_quantities
                == {
                    money(
                        "100"
                    )
                }
            )

            assert len(
                active_on_hand
            ) == 2

            assert money(
                active_on_hand_delta
            ) == money(
                "-10"
            )

            total_active_delta = sum(
                (
                    money(
                        Decimal(
                            row[
                                "corrected_valuation_amount"
                            ]
                        )
                        - Decimal(
                            row[
                                "original_valuation_amount"
                            ]
                        )
                    )
                    for row in d5_replay
                ),
                ZERO,
            )

            assert money(
                total_active_delta
            ) == money(
                "-10"
            )

            # Every active on-hand row is quantity-neutral in base
            # MA history and has no ISSUE/ICE provenance.
            for row in active_on_hand:
                assert (
                    row[
                        "source_moving_average_movement_id"
                    ]
                    is None
                )
                assert (
                    row[
                        "source_inventory_cost_entry_id"
                    ]
                    is None
                )

            # -------------------------------------------------
            # ACCOUNT PROVENANCE FOR D5 ASSERTIONS
            #
            # Do not hardcode 281 / 631 and do not resolve current
            # accounting roles. Read the accounts from immutable
            # D4 PVC accounting history.
            #
            # D4 active on_hand PVC is negative:
            #     Dr supplier payable / Cr inventory.
            #
            # D4 active issued PVC is negative:
            #     Dr supplier payable /
            #     Cr historical ISSUE destination.
            # -------------------------------------------------

            d4_on_hand_event_ids = tuple(
                int(
                    row[
                        "id"
                    ]
                )
                for row in replay_history
                if (
                    row[
                        "recognition_date"
                    ]
                    == d4
                    and row[
                        "effect_kind"
                    ]
                    == "on_hand"
                    and row[
                        "reversal_of_id"
                    ]
                    is None
                )
            )

            d4_issued_event_ids = tuple(
                int(
                    row[
                        "id"
                    ]
                )
                for row in replay_history
                if (
                    row[
                        "recognition_date"
                    ]
                    == d4
                    and row[
                        "effect_kind"
                    ]
                    == "issued"
                    and row[
                        "reversal_of_id"
                    ]
                    is None
                )
            )

            assert d4_on_hand_event_ids
            assert d4_issued_event_ids

            d4_on_hand_credit_accounts = tuple(
                await rows(
                    db,
                    """
                    SELECT DISTINCT
                        jel.account_id
                    FROM journal_entries je
                    JOIN journal_entry_lines jel
                      ON jel.journal_entry_id = je.id
                    WHERE je.company_id = :company_id
                      AND je.purchase_value_correction_ma_replay_event_id
                          = ANY(:event_ids)
                      AND jel.credit > 0
                    ORDER BY jel.account_id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "event_ids":
                            list(
                                d4_on_hand_event_ids
                            ),
                    },
                )
            )

            assert len(
                d4_on_hand_credit_accounts
            ) == 1

            inventory_account_id = int(
                d4_on_hand_credit_accounts[
                    0
                ][
                    "account_id"
                ]
            )

            d4_issued_debit_accounts = tuple(
                await rows(
                    db,
                    """
                    SELECT DISTINCT
                        jel.account_id
                    FROM journal_entries je
                    JOIN journal_entry_lines jel
                      ON jel.journal_entry_id = je.id
                    WHERE je.company_id = :company_id
                      AND je.purchase_value_correction_ma_replay_event_id
                          = ANY(:event_ids)
                      AND jel.debit > 0
                    ORDER BY jel.account_id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "event_ids":
                            list(
                                d4_issued_event_ids
                            ),
                    },
                )
            )

            assert len(
                d4_issued_debit_accounts
            ) == 1

            supplier_payable_account_id = int(
                d4_issued_debit_accounts[
                    0
                ][
                    "account_id"
                ]
            )

            assert (
                inventory_account_id
                != supplier_payable_account_id
            )

            assert (
                historical_issue_destination_account_id
                != supplier_payable_account_id
            )

            # -------------------------------------------------
            # FORWARD-ONLY REPLAY + TYPED JOURNALS
            # -------------------------------------------------

            replay_count_after_d5 = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM purchase_value_correction_ma_replay_events
                WHERE company_id = :company_id
                  AND product_id = :product_id
                  AND warehouse_id = :warehouse_id
                """,
                {
                    "company_id": COMPANY_ID,
                    "product_id": product_id,
                    "warehouse_id": warehouse_id,
                },
            )

            pvc_je_count_after_d5 = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM journal_entries
                WHERE company_id = :company_id
                  AND purchase_value_correction_ma_replay_event_id
                      IS NOT NULL
                """,
                {
                    "company_id": COMPANY_ID,
                },
            )

            normal_je_count_after_d5 = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM journal_entries
                WHERE company_id = :company_id
                  AND purchase_value_correction_ma_replay_event_id
                      IS NULL
                """,
                {
                    "company_id": COMPANY_ID,
                },
            )

            assert (
                replay_count_after_d5
                > replay_count_before_d5
            )

            assert (
                pvc_je_count_after_d5
                > pvc_je_count_before_d5
            )

            # Base accounting reversal also happened.
            assert (
                normal_je_count_after_d5
                > normal_je_count_before_d5
            )

            d5_replay_rows = tuple(
                await rows(
                    db,
                    """
                    SELECT
                        id,
                        effect_kind,
                        recognition_date,
                        reversal_of_id
                    FROM purchase_value_correction_ma_replay_events
                    WHERE company_id = :company_id
                      AND product_id = :product_id
                      AND warehouse_id = :warehouse_id
                      AND recognition_date = :d5
                    ORDER BY id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "product_id": product_id,
                        "warehouse_id": warehouse_id,
                        "d5": d5,
                    },
                )
            )

            assert d5_replay_rows

            for row in d5_replay_rows:
                typed_count = await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM journal_entries
                    WHERE company_id = :company_id
                      AND purchase_value_correction_ma_replay_event_id
                          = :event_id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "event_id": int(
                            row[
                                "id"
                            ]
                        ),
                    },
                )

                assert typed_count == 1

            # -------------------------------------------------
            # ACCOUNTING EFFECT
            #
            # D4 active PVC:
            #     issued  -4
            #     on_hand -6
            #
            # D5 desired:
            #     on_hand -10
            #
            # Technical reversal therefore reclassifies the
            # correction back from historical ISSUE destination
            # into inventory while preserving total supplier-side
            # correction.
            # -------------------------------------------------

            d5_pvc_lines = tuple(
                await rows(
                    db,
                    """
                    SELECT
                        jel.account_id,
                        jel.debit,
                        jel.credit,
                        je.purchase_value_correction_ma_replay_event_id
                    FROM journal_entries je
                    JOIN journal_entry_lines jel
                      ON jel.journal_entry_id = je.id
                    JOIN purchase_value_correction_ma_replay_events e
                      ON e.id =
                         je.purchase_value_correction_ma_replay_event_id
                    WHERE je.company_id = :company_id
                      AND e.recognition_date = :d5
                    ORDER BY je.id, jel.line_no
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "d5": d5,
                    },
                )
            )

            assert d5_pvc_lines

            d5_debits_by_account = {}
            d5_credits_by_account = {}

            for row in d5_pvc_lines:
                account_id = int(
                    row[
                        "account_id"
                    ]
                )

                d5_debits_by_account[
                    account_id
                ] = money(
                    d5_debits_by_account.get(
                        account_id,
                        ZERO,
                    )
                    + Decimal(
                        row[
                            "debit"
                        ]
                    )
                )

                d5_credits_by_account[
                    account_id
                ] = money(
                    d5_credits_by_account.get(
                        account_id,
                        ZERO,
                    )
                    + Decimal(
                        row[
                            "credit"
                        ]
                    )
                )

            # Net D5 PVC reclassification:
            #
            # reverse issued -4:
            #     Dr historical COGS 4 / Cr 631 4
            #
            # reverse old on_hand -6:
            #     Dr 281 6 / Cr 631 6
            #
            # new on_hand -10:
            #     Dr 631 10 / Cr 281 10
            #
            # Net:
            #     Dr historical COGS 4
            #     Cr inventory 4
            #     supplier payable net 0.
            assert money(
                d5_debits_by_account.get(
                    historical_issue_destination_account_id,
                    ZERO,
                )
                - d5_credits_by_account.get(
                    historical_issue_destination_account_id,
                    ZERO,
                )
            ) == money(
                "4"
            )

            assert money(
                d5_credits_by_account.get(
                    inventory_account_id,
                    ZERO,
                )
                - d5_debits_by_account.get(
                    inventory_account_id,
                    ZERO,
                )
            ) == money(
                "4"
            )

            assert money(
                d5_debits_by_account.get(
                    supplier_payable_account_id,
                    ZERO,
                )
                - d5_credits_by_account.get(
                    supplier_payable_account_id,
                    ZERO,
                )
            ) == money(
                "0"
            )

            # -------------------------------------------------
            # SECOND TECHNICAL REVERSAL IS NOT RE-EXECUTED.
            #
            # Idempotency here means PVC reconciliation itself is
            # already at desired state after production reversal.
            # Reconcile the original ISSUE provenance once more.
            # -------------------------------------------------

            from app.services.purchase_value_correction_moving_average_issue_reversal_integration_service import (
                reconcile_purchase_value_correction_moving_average_after_issue_reversal,
            )
            from app.services.purchase_value_correction_moving_average_lifecycle_service import (
                post_created_purchase_value_correction_moving_average_peer_journals,
            )

            replay_count_before_noop = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM purchase_value_correction_ma_replay_events
                WHERE company_id = :company_id
                  AND product_id = :product_id
                  AND warehouse_id = :warehouse_id
                """,
                {
                    "company_id": COMPANY_ID,
                    "product_id": product_id,
                    "warehouse_id": warehouse_id,
                },
            )

            pvc_je_count_before_noop = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM journal_entries
                WHERE company_id = :company_id
                  AND purchase_value_correction_ma_replay_event_id
                      IS NOT NULL
                """,
                {
                    "company_id": COMPANY_ID,
                },
            )

            noop = (
                await reconcile_purchase_value_correction_moving_average_after_issue_reversal(
                    db,
                    company_id=COMPANY_ID,
                    source_moving_average_movement_id=int(
                        issue_ma_snapshot[
                            "id"
                        ]
                    ),
                    reversal_date=d5,
                    created_by=USER_ID,
                )
            )

            if noop.reconciliation_result is not None:
                noop_journals = (
                    await post_created_purchase_value_correction_moving_average_peer_journals(
                        db,
                        result=noop.reconciliation_result,
                        created_by=USER_ID,
                    )
                )
            else:
                noop_journals = ()

            await db.flush()

            assert (
                noop.reconciliation_result
                is None
                or noop.reconciliation_result.created_events
                == ()
            )

            assert noop_journals == ()

            replay_count_after_noop = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM purchase_value_correction_ma_replay_events
                WHERE company_id = :company_id
                  AND product_id = :product_id
                  AND warehouse_id = :warehouse_id
                """,
                {
                    "company_id": COMPANY_ID,
                    "product_id": product_id,
                    "warehouse_id": warehouse_id,
                },
            )

            pvc_je_count_after_noop = await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM journal_entries
                WHERE company_id = :company_id
                  AND purchase_value_correction_ma_replay_event_id
                      IS NOT NULL
                """,
                {
                    "company_id": COMPANY_ID,
                },
            )

            assert (
                replay_count_after_noop
                == replay_count_before_noop
            )

            assert (
                pvc_je_count_after_noop
                == pvc_je_count_before_noop
            )

            print(
                "D5 TECHNICAL ISSUE REVERSAL = PASS"
            )
            print(
                "D5 BASE MA 60 -> 100 = PASS"
            )
            print(
                "D5 ORIGINAL ISSUE MA IMMUTABLE = PASS"
            )
            print(
                "D5 MA REVERSAL ROW = PASS"
            )
            print(
                "D5 ICE IMMUTABLE = PASS"
            )
            print(
                "D5 ACTIVE ISSUED PVC = 0 = PASS"
            )
            print(
                "D5 ACTIVE ON_HAND PVC = -10 = PASS"
            )
            print(
                "D5 PVC CONSERVATION -10 = PASS"
            )
            print(
                "D5 PVC TYPED JOURNALS = PASS"
            )
            print(
                "D5 HISTORICAL ISSUE DESTINATION = PASS"
            )
            print(
                "D5 SECOND PVC RECONCILE FULL NOOP = PASS"
            )


        except Exception as exc:
            scenario_error = exc

            import traceback

            scenario_traceback = (
                traceback.format_exc()
            )

        finally:
            await db.close()

            if transaction.is_active:
                await transaction.rollback()

    after = (
        await complete_table_counts()
    )

    assert after == baseline, (
        "\nPostgreSQL MA technical reversal rollback "
        "did not restore exact baseline.\n"
        f"before={baseline}\n"
        f"after={after}"
    )

    if scenario_error is not None:
        pytest.fail(
            "\nREAL POSTGRESQL MA TECHNICAL REVERSAL FAILED\n"
            + (
                scenario_traceback
                or repr(
                    scenario_error
                )
            )
        )

    print(
        "FULL TRANSACTION ROLLBACK = PASS"
    )
