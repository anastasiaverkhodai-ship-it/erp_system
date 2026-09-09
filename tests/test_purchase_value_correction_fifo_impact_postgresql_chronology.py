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
from app.services.company_default_accounting_rules_service import (
    SALES_FULFILLMENT_RULE_CODE,
    seed_company_default_accounting_rules,
)
from app.services.invoice_fulfillment_allocation_service import (
    create_invoice_fulfillment_allocation,
)
from app.services.purchase_value_correction_allocation_reconciliation_service import (
    reconcile_purchase_value_correction_allocations_for_event,
)
from app.services.purchase_value_correction_fifo_impact_reconciliation_service import (
    reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line,
)
from app.services.reservation_persistence_service import (
    reserve_source_line,
)
from app.services.trade_fulfillment_service import (
    PurchaseOrderFulfillmentRequestLine,
    SalesOrderFulfillmentRequestLine,
    execute_purchase_order_fulfillment,
    execute_sales_order_fulfillment,
    execute_sales_order_fulfillment_reversal,
)


RUN_POSTGRES_E2E = (
    os.getenv(
        "RUN_POSTGRES_E2E"
    )
    == "1"
)

COMPANY_ID = 1
USER_ID = 1

QTY_ZERO = Decimal("0.0000")
MONEY_ZERO = Decimal("0.00")

BASE_PATH = Path(
    __file__
).with_name(
    "test_purchase_value_correction_allocation_postgresql_chronology.py"
)

SPEC = importlib.util.spec_from_file_location(
    "_pvc_allocation_pg_base",
    BASE_PATH,
)

if (
    SPEC is None
    or SPEC.loader is None
):
    raise RuntimeError(
        "Cannot load allocation PostgreSQL chronology helpers"
    )

base = importlib.util.module_from_spec(
    SPEC
)

sys.modules[
    SPEC.name
] = base

SPEC.loader.exec_module(
    base
)


EXTRA_BASELINE_TABLES = (
    "accounting_rules",
    "accounting_rule_lines",
    "reservation_movements",
    "stock_lot_consumptions",
    "purchase_value_correction_fifo_impact_events",
)


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
            (
                params
                or {}
            ),
        )
    ).scalar_one()


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
            (
                params
                or {}
            ),
        )
    ).mappings().one()


async def extra_table_counts():
    values = {}

    async with engine.connect() as connection:
        for table_name in EXTRA_BASELINE_TABLES:
            values[
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

    return values


async def complete_table_counts():
    return {
        "existing": (
            await base.table_counts()
        ),
        "extra": (
            await extra_table_counts()
        ),
    }


async def find_or_seed_issue_accounting_rule(
    db,
):
    rule_id = (
        await db.execute(
            text(
                """
                SELECT id
                FROM accounting_rules
                WHERE company_id =
                      :company_id
                  AND code =
                      :code
                  AND is_active IS TRUE
                ORDER BY id
                LIMIT 1
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
                "code": (
                    SALES_FULFILLMENT_RULE_CODE
                ),
            },
        )
    ).scalar_one_or_none()

    if rule_id is not None:
        return int(
            rule_id
        )

    await seed_company_default_accounting_rules(
        session=db,
        company_id=COMPANY_ID,
    )

    await db.flush()

    rule_id = (
        await db.execute(
            text(
                """
                SELECT id
                FROM accounting_rules
                WHERE company_id =
                      :company_id
                  AND code =
                      :code
                  AND is_active IS TRUE
                ORDER BY id
                LIMIT 1
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
                "code": (
                    SALES_FULFILLMENT_RULE_CODE
                ),
            },
        )
    ).scalar_one_or_none()

    assert rule_id is not None, (
        "Could not resolve seeded "
        "SALES_FULFILLMENT_ISSUE accounting rule"
    )

    return int(
        rule_id
    )


async def create_sales_order(
    db,
    *,
    fixture,
    quantity,
):
    customer_id = await scalar(
        db,
        """
        INSERT INTO counterparties (
            company_id,
            name
        )
        VALUES (
            :company_id,
            :name
        )
        RETURNING id
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "name": (
                "PVC FIFO Customer "
                + fixture["suffix"]
            ),
        },
    )

    order_id = await scalar(
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
        VALUES (
            :company_id,
            :counterparty_id,
            NULL,
            :number,
            'sale',
            'order',
            'confirmed',
            :document_date,
            'UAH',
            0,
            :created_by,
            CURRENT_TIMESTAMP
        )
        RETURNING id
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "counterparty_id": (
                customer_id
            ),
            "number": (
                "SO-PVC-FIFO-"
                + fixture["suffix"]
            ),
            "document_date": (
                fixture[
                    "business_date"
                ]
            ),
            "created_by": (
                USER_ID
            ),
        },
    )

    line_id = await scalar(
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
            :document_id,
            1,
            :product_id,
            :warehouse_id,
            :quantity,
            1.0000
        )
        RETURNING id
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "document_id": (
                order_id
            ),
            "product_id": (
                fixture[
                    "product_id"
                ]
            ),
            "warehouse_id": (
                fixture[
                    "warehouse_id"
                ]
            ),
            "quantity": (
                quantity
            ),
        },
    )

    return (
        order_id,
        line_id,
    )


async def preexisting_fifo_quantity(
    db,
    *,
    product_id,
    warehouse_id,
):
    value = await scalar(
        db,
        """
        SELECT COALESCE(
            SUM(remaining_quantity),
            0
        )
        FROM stock_lots
        WHERE company_id =
              :company_id
          AND product_id =
              :product_id
          AND warehouse_id =
              :warehouse_id
          AND remaining_quantity > 0
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "product_id": (
                product_id
            ),
            "warehouse_id": (
                warehouse_id
            ),
        },
    )

    return Decimal(
        value
    )


async def receipt_lot(
    db,
    *,
    fulfillment_line_id,
):
    return await mapping_one(
        db,
        """
        SELECT
            sl.id,
            sl.original_quantity,
            sl.remaining_quantity
        FROM trade_fulfillment_lines tfl
        JOIN stock_lots sl
          ON sl.company_id =
             tfl.company_id
         AND sl.source_document_id =
             tfl.warehouse_document_id
         AND sl.source_document_line_id =
             tfl.warehouse_document_line_id
        WHERE tfl.company_id =
              :company_id
          AND tfl.id =
              :fulfillment_line_id
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "fulfillment_line_id": (
                fulfillment_line_id
            ),
        },
    )


async def active_consumed_quantity(
    db,
    *,
    stock_lot_id,
):
    value = await scalar(
        db,
        """
        SELECT COALESCE(
            SUM(slc.quantity),
            0
        )
        FROM stock_lot_consumptions slc
        JOIN documents d
          ON d.company_id =
             slc.company_id
         AND d.id =
             slc.issue_document_id
        WHERE slc.company_id =
              :company_id
          AND slc.stock_lot_id =
              :stock_lot_id
          AND d.document_type =
              'issue'
          AND d.status =
              'posted'
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "stock_lot_id": (
                stock_lot_id
            ),
        },
    )

    return Decimal(
        value
    )


async def impact_history(
    db,
    *,
    allocation_event_id,
):
    return tuple(
        (
            await db.execute(
                text(
                    """
                    SELECT
                        id,
                        purchase_value_correction_allocation_event_id,
                        stock_lot_id,
                        destination_kind,
                        stock_lot_consumption_id,
                        issue_document_id,
                        issue_document_line_id,
                        recognition_date,
                        quantity,
                        original_base_amount,
                        corrected_base_amount,
                        currency_code,
                        reversal_of_id
                    FROM purchase_value_correction_fifo_impact_events
                    WHERE company_id =
                          :company_id
                      AND purchase_value_correction_allocation_event_id =
                          :allocation_event_id
                    ORDER BY id
                    """
                ),
                {
                    "company_id": (
                        COMPANY_ID
                    ),
                    "allocation_event_id": (
                        allocation_event_id
                    ),
                },
            )
        )
        .mappings()
        .all()
    )


def active_impact_rows(
    rows,
):
    by_id = {
        int(
            row[
                "id"
            ]
        ): row
        for row in rows
    }

    reversed_ids = {
        int(
            row[
                "reversal_of_id"
            ]
        )
        for row in rows
        if (
            row[
                "reversal_of_id"
            ]
            is not None
        )
    }

    return tuple(
        row
        for event_id, row
        in sorted(
            by_id.items()
        )
        if (
            row[
                "reversal_of_id"
            ]
            is None
            and event_id
            not in reversed_ids
        )
    )


def assert_money(
    value,
    expected,
):
    assert Decimal(
        value
    ) == Decimal(
        expected
    )


@pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run the real PostgreSQL "
        "FIFO impact chronology test"
    ),
)
@pytest.mark.asyncio
async def test_purchase_value_correction_fifo_impact_postgresql_chronology():
    await engine.dispose(
        close=False,
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
                await base.create_business_fixture(
                    db
                )
            )

            d1 = fixture[
                "business_date"
            ]

            d2 = (
                d1
                + timedelta(
                    days=1
                )
            )

            d3 = (
                d1
                + timedelta(
                    days=2
                )
            )

            d4 = (
                d1
                + timedelta(
                    days=3
                )
            )

            period_end = (
                await base.open_period_end(
                    db,
                    business_date=d1,
                )
            )

            assert period_end is not None

            if d4 > period_end:
                pytest.skip(
                    "Real PostgreSQL FIFO E2E "
                    "requires four usable business "
                    "dates inside the open period"
                )

            old_fifo_quantity = (
                await preexisting_fifo_quantity(
                    db,
                    product_id=(
                        fixture[
                            "product_id"
                        ]
                    ),
                    warehouse_id=(
                        fixture[
                            "warehouse_id"
                        ]
                    ),
                )
            )

            assert (
                old_fifo_quantity
                >= QTY_ZERO
            )

            receipt = (
                await execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=(
                        fixture[
                            "order_id"
                        ]
                    ),
                    warehouse_document_number=(
                        "PVC-FIFO-PG-R-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    document_date=d1,
                    accounting_rule_id=(
                        fixture[
                            "accounting_rule_id"
                        ]
                    ),
                    created_by=USER_ID,
                    request_lines=(
                        PurchaseOrderFulfillmentRequestLine(
                            trade_document_line_id=(
                                fixture[
                                    "order_line_id"
                                ]
                            ),
                            quantity=Decimal(
                                "120.0000"
                            ),
                        ),
                    ),
                )
            )

            fulfillment_line_id = (
                await base.fulfillment_line_id(
                    db,
                    fulfillment_id=(
                        receipt
                        .fulfillment
                        .id
                    ),
                )
            )

            lot = await receipt_lot(
                db,
                fulfillment_line_id=(
                    fulfillment_line_id
                ),
            )

            stock_lot_id = int(
                lot[
                    "id"
                ]
            )

            assert Decimal(
                lot[
                    "original_quantity"
                ]
            ) == Decimal(
                "120.0000"
            )

            assert Decimal(
                lot[
                    "remaining_quantity"
                ]
            ) == Decimal(
                "120.0000"
            )

            allocation = (
                await create_invoice_fulfillment_allocation(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=(
                        fixture[
                            "invoice_id"
                        ]
                    ),
                    invoice_line_id=(
                        fixture[
                            "invoice_line_id"
                        ]
                    ),
                    fulfillment_id=(
                        receipt
                        .fulfillment
                        .id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    quantity=Decimal(
                        "120.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            correction = (
                await base.insert_value_correction(
                    db,
                    invoice_id=(
                        fixture[
                            "invoice_id"
                        ]
                    ),
                    invoice_line_id=(
                        fixture[
                            "invoice_line_id"
                        ]
                    ),
                    product_id=(
                        fixture[
                            "product_id"
                        ]
                    ),
                    correction_date=d2,
                    original_gross_amount=(
                        "120.00"
                    ),
                    corrected_gross_amount=(
                        "108.00"
                    ),
                    reason_code=(
                        "postgres_fifo_impact"
                    ),
                )
            )

            allocation_result = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction.id
                    ),
                    adjustment_date=d2,
                    created_by=USER_ID,
                )
            )

            assert len(
                allocation_result.created_events
            ) == 1

            pvc_allocation = (
                allocation_result
                .created_events[
                    0
                ]
            )

            assert (
                pvc_allocation
                .invoice_fulfillment_allocation_id
                == allocation.id
            )

            assert_money(
                pvc_allocation
                .original_allocated_base_amount,
                "120.00",
            )

            assert_money(
                pvc_allocation
                .corrected_allocated_base_amount,
                "108.00",
            )

            initial = (
                await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d2,
                    created_by=USER_ID,
                )
            )

            assert len(
                initial.created_events
            ) == 1

            rows_initial = (
                await impact_history(
                    db,
                    allocation_event_id=(
                        pvc_allocation.id
                    ),
                )
            )

            active_initial = (
                active_impact_rows(
                    rows_initial
                )
            )

            assert len(
                active_initial
            ) == 1

            initial_on_hand = (
                active_initial[
                    0
                ]
            )

            assert (
                initial_on_hand[
                    "destination_kind"
                ]
                == "on_hand"
            )

            assert Decimal(
                initial_on_hand[
                    "quantity"
                ]
            ) == Decimal(
                "120.0000"
            )

            assert_money(
                initial_on_hand[
                    "original_base_amount"
                ],
                "120.00",
            )

            assert_money(
                initial_on_hand[
                    "corrected_base_amount"
                ],
                "108.00",
            )

            assert (
                initial_on_hand[
                    "recognition_date"
                ]
                == d2
            )

            repeat_initial = (
                await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d2,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_initial.created_events
                == ()
            )

            print(
                "REAL PG FIFO INITIAL ON-HAND: "
                "120 -> 108 / IDEMPOTENT = PASS"
            )

            issue_quantity = (
                old_fifo_quantity
                + Decimal(
                    "50.0000"
                )
            )

            sales_order_id, sales_line_id = (
                await create_sales_order(
                    db,
                    fixture=fixture,
                    quantity=(
                        issue_quantity
                    ),
                )
            )

            await reserve_source_line(
                db,
                company_id=COMPANY_ID,
                source_document_id=(
                    sales_order_id
                ),
                source_document_line_id=(
                    sales_line_id
                ),
                quantity=(
                    issue_quantity
                ),
            )

            issue_rule_id = (
                await find_or_seed_issue_accounting_rule(
                    db
                )
            )

            issue = (
                await execute_sales_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=(
                        sales_order_id
                    ),
                    warehouse_document_number=(
                        "PVC-FIFO-PG-I-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    document_date=d3,
                    accounting_rule_id=(
                        issue_rule_id
                    ),
                    created_by=USER_ID,
                    request_lines=(
                        SalesOrderFulfillmentRequestLine(
                            trade_document_line_id=(
                                sales_line_id
                            ),
                            quantity=(
                                issue_quantity
                            ),
                        ),
                    ),
                )
            )

            lot_after_issue = (
                await receipt_lot(
                    db,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                )
            )

            assert Decimal(
                lot_after_issue[
                    "remaining_quantity"
                ]
            ) == Decimal(
                "70.0000"
            )

            assert (
                await active_consumed_quantity(
                    db,
                    stock_lot_id=(
                        stock_lot_id
                    ),
                )
            ) == Decimal(
                "50.0000"
            )

            after_issue = (
                await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d3,
                    created_by=USER_ID,
                )
            )

            assert len(
                after_issue.created_events
            ) == 3

            rows_issue = (
                await impact_history(
                    db,
                    allocation_event_id=(
                        pvc_allocation.id
                    ),
                )
            )

            active_issue = (
                active_impact_rows(
                    rows_issue
                )
            )

            assert len(
                active_issue
            ) == 2

            by_kind = {
                row[
                    "destination_kind"
                ]: row
                for row in active_issue
            }

            assert set(
                by_kind
            ) == {
                "issued",
                "on_hand",
            }

            issued = (
                by_kind[
                    "issued"
                ]
            )

            on_hand = (
                by_kind[
                    "on_hand"
                ]
            )

            assert Decimal(
                issued[
                    "quantity"
                ]
            ) == Decimal(
                "50.0000"
            )

            assert_money(
                issued[
                    "original_base_amount"
                ],
                "50.00",
            )

            assert_money(
                issued[
                    "corrected_base_amount"
                ],
                "45.00",
            )

            assert (
                issued[
                    "issue_document_id"
                ]
                == (
                    issue
                    .warehouse_document
                    .id
                )
            )

            assert (
                issued[
                    "stock_lot_consumption_id"
                ]
                is not None
            )

            assert (
                issued[
                    "recognition_date"
                ]
                == d3
            )

            assert Decimal(
                on_hand[
                    "quantity"
                ]
            ) == Decimal(
                "70.0000"
            )

            assert_money(
                on_hand[
                    "original_base_amount"
                ],
                "70.00",
            )

            assert_money(
                on_hand[
                    "corrected_base_amount"
                ],
                "63.00",
            )

            assert (
                on_hand[
                    "recognition_date"
                ]
                == d3
            )

            assert sum(
                (
                    Decimal(
                        row[
                            "quantity"
                        ]
                    )
                    for row
                    in active_issue
                ),
                QTY_ZERO,
            ) == Decimal(
                "120.0000"
            )

            assert sum(
                (
                    Decimal(
                        row[
                            "original_base_amount"
                        ]
                    )
                    for row
                    in active_issue
                ),
                MONEY_ZERO,
            ) == Decimal(
                "120.00"
            )

            assert sum(
                (
                    Decimal(
                        row[
                            "corrected_base_amount"
                        ]
                    )
                    for row
                    in active_issue
                ),
                MONEY_ZERO,
            ) == Decimal(
                "108.00"
            )

            repeat_issue = (
                await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d3,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_issue.created_events
                == ()
            )

            print(
                "REAL PG FIFO ISSUE: "
                "50 ISSUED + 70 ON-HAND / "
                "EXACT CONSERVATION / IDEMPOTENT = PASS"
            )

            reversal = (
                await execute_sales_order_fulfillment_reversal(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=(
                        sales_order_id
                    ),
                    fulfillment_id=(
                        issue
                        .fulfillment
                        .id
                    ),
                    reversal_date=d4,
                    reversed_by=USER_ID,
                )
            )

            assert str(
                reversal
                .warehouse_document
                .status
            ) in {
                "reversed",
                "DocumentStatus.REVERSED",
            }

            lot_after_reversal = (
                await receipt_lot(
                    db,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                )
            )

            assert Decimal(
                lot_after_reversal[
                    "remaining_quantity"
                ]
            ) == Decimal(
                "120.0000"
            )

            assert (
                await active_consumed_quantity(
                    db,
                    stock_lot_id=(
                        stock_lot_id
                    ),
                )
            ) == QTY_ZERO

            after_reversal = (
                await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d4,
                    created_by=USER_ID,
                )
            )

            assert len(
                after_reversal.created_events
            ) == 3

            rows_final = (
                await impact_history(
                    db,
                    allocation_event_id=(
                        pvc_allocation.id
                    ),
                )
            )

            active_final = (
                active_impact_rows(
                    rows_final
                )
            )

            assert len(
                active_final
            ) == 1

            final_on_hand = (
                active_final[
                    0
                ]
            )

            assert (
                final_on_hand[
                    "destination_kind"
                ]
                == "on_hand"
            )

            assert Decimal(
                final_on_hand[
                    "quantity"
                ]
            ) == Decimal(
                "120.0000"
            )

            assert_money(
                final_on_hand[
                    "original_base_amount"
                ],
                "120.00",
            )

            assert_money(
                final_on_hand[
                    "corrected_base_amount"
                ],
                "108.00",
            )

            assert (
                final_on_hand[
                    "recognition_date"
                ]
                == d4
            )

            repeat_final = (
                await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=d4,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_final.created_events
                == ()
            )

            print(
                "REAL PG FIFO ISSUE REVERSAL: "
                "ACTIVE CONSUMPTION = 0 / "
                "120 ON-HAND FORWARD REPLACEMENT / "
                "IDEMPOTENT = PASS"
            )

        except BaseException as exc:
            scenario_error = exc
            scenario_traceback = (
                exc.__traceback__
            )

        finally:
            await db.close()

            if transaction.is_active:
                await transaction.rollback()

    after = (
        await complete_table_counts()
    )

    assert after == baseline, (
        "\nPostgreSQL FIFO impact E2E rollback "
        "did not restore business data.\n"
        f"before={baseline}\n"
        f"after={after}"
    )

    print(
        "POSTGRESQL FIFO IMPACT BUSINESS DATA "
        "ROLLBACK = PASS"
    )

    if scenario_error is not None:
        raise scenario_error.with_traceback(
            scenario_traceback
        )
