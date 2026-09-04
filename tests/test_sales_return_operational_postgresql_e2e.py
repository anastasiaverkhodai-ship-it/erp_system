import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.database import (
    AsyncSessionLocal,
)
from app.services.sales_return_operational_service import (
    apply_sales_return_operational_event,
)


COMPANY_ID = 1
USER_ID = 1

RUN_REAL_POSTGRES = (
    os.getenv(
        "RUN_POSTGRES_E2E"
    )
    == "1"
)


pytestmark = pytest.mark.skipif(
    not RUN_REAL_POSTGRES,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run the real PostgreSQL "
        "Sales Return operational test"
    ),
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


async def insert_id(
    db,
    sql,
    params,
):
    return int(
        await scalar(
            db,
            sql,
            params,
        )
    )


async def table_counts(
    db,
):
    tables = (
        "counterparties",
        "contracts",
        "trade_documents",
        "trade_document_lines",
        "documents",
        "document_lines",
        "trade_fulfillments",
        "trade_fulfillment_lines",
        "invoice_fulfillment_allocations",
        "sales_recognition_events",
        "stock_lots",
        "stock_lot_consumptions",
        "inventory_cost_entries",
        "stock_balances",
        "stock_ledger",
        "trade_return_events",
        "sales_return_recognition_events",
        "sales_return_cost_restoration_events",
        "sales_return_cost_restoration_fifo_slices",
        "journal_entries",
        "journal_entry_lines",
    )

    result = {}

    for table in tables:
        result[
            table
        ] = int(
            await scalar(
                db,
                f"""
                SELECT COUNT(*)
                FROM {table}
                """
            )
        )

    return result


async def journal_amounts(
    db,
    *,
    source_column,
    source_id,
):
    if source_column not in {
        "sales_return_recognition_event_id",
        "sales_return_cost_restoration_event_id",
    }:
        raise AssertionError(
            "Unexpected journal source column"
        )

    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    a.code,
                    COALESCE(
                        SUM(jel.debit),
                        0
                    ) AS debit,
                    COALESCE(
                        SUM(jel.credit),
                        0
                    ) AS credit
                FROM journal_entries je
                JOIN journal_entry_lines jel
                  ON jel.journal_entry_id =
                     je.id
                JOIN accounts a
                  ON a.id =
                     jel.account_id
                 AND a.company_id =
                     je.company_id
                WHERE je.company_id =
                      :company_id
                  AND je.{source_column} =
                      :source_id
                GROUP BY a.code
                ORDER BY a.code
                """
            ),
            {
                "company_id":
                    COMPANY_ID,
                "source_id":
                    source_id,
            },
        )
    ).mappings().all()

    return {
        row[
            "code"
        ]: (
            Decimal(
                row[
                    "debit"
                ]
            ),
            Decimal(
                row[
                    "credit"
                ]
            ),
        )
        for row in rows
    }


@pytest.mark.asyncio
async def test_sales_return_original_and_reversal_postgresql():
    token = uuid4().hex[
        :12
    ]

    async with AsyncSessionLocal() as db:
        before = await table_counts(
            db
        )

        try:
            # -------------------------------------------------
            # 1. REAL ENVIRONMENT PRECONDITIONS
            # -------------------------------------------------

            company = (
                await db.execute(
                    text(
                        """
                        SELECT
                            inventory_valuation_method,
                            chart_of_accounts_template
                        FROM companies
                        WHERE id =
                              :company_id
                          AND is_active IS TRUE
                        """
                    ),
                    {
                        "company_id":
                            COMPANY_ID,
                    },
                )
            ).mappings().one()

            assert (
                company[
                    "inventory_valuation_method"
                ]
                == "fifo"
            )

            assert (
                company[
                    "chart_of_accounts_template"
                ]
                == "general_291"
            )

            user_exists = bool(
                await scalar(
                    db,
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM users
                        WHERE id =
                              :user_id
                          AND is_active IS TRUE
                    )
                    """,
                    {
                        "user_id":
                            USER_ID,
                    },
                )
            )

            assert user_exists

            business_date = (
                await scalar_or_none(
                    db,
                    """
                    SELECT start_date
                    FROM accounting_periods
                    WHERE company_id =
                          :company_id
                      AND status =
                          'open'
                      AND is_locked IS FALSE
                    ORDER BY
                        start_date DESC,
                        id DESC
                    LIMIT 1
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                    },
                )
            )

            assert (
                business_date
                is not None
            ), (
                "Real PostgreSQL E2E requires "
                "one open unlocked accounting period"
            )

            account_rows = (
                await db.execute(
                    text(
                        """
                        SELECT
                            code,
                            is_active,
                            is_postable
                        FROM accounts
                        WHERE company_id =
                              :company_id
                          AND code IN (
                              '281',
                              '361',
                              '704',
                              '902'
                          )
                        ORDER BY code
                        """
                    ),
                    {
                        "company_id":
                            COMPANY_ID,
                    },
                )
            ).mappings().all()

            assert {
                row[
                    "code"
                ]
                for row in account_rows
            } == {
                "281",
                "361",
                "704",
                "902",
            }

            assert all(
                row[
                    "is_active"
                ]
                is True
                and row[
                    "is_postable"
                ]
                is True
                for row
                in account_rows
            )

            product_id = int(
                await scalar(
                    db,
                    """
                    SELECT id
                    FROM products
                    WHERE company_id =
                          :company_id
                      AND is_active IS TRUE
                    ORDER BY id
                    LIMIT 1
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                    },
                )
            )

            warehouse_id = int(
                await scalar(
                    db,
                    """
                    SELECT id
                    FROM warehouses
                    WHERE company_id =
                          :company_id
                      AND is_active IS TRUE
                    ORDER BY id
                    LIMIT 1
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                    },
                )
            )

            # -------------------------------------------------
            # 2. COMMERCIAL SALES FIXTURE
            # -------------------------------------------------

            counterparty_id = await insert_id(
                db,
                """
                INSERT INTO counterparties (
                    company_id,
                    name,
                    short_name,
                    counterparty_type,
                    vat_status,
                    default_currency_code,
                    payment_term_days,
                    credit_limit,
                    is_active
                )
                VALUES (
                    :company_id,
                    :name,
                    :short_name,
                    'customer',
                    'non_vat_payer',
                    'UAH',
                    0,
                    0,
                    TRUE
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "name":
                        (
                            "Sales Return E2E "
                            f"{token}"
                        ),
                    "short_name":
                        f"SR-{token}",
                },
            )

            contract_id = await insert_id(
                db,
                """
                INSERT INTO contracts (
                    company_id,
                    counterparty_id,
                    number,
                    name,
                    contract_type,
                    status,
                    start_date,
                    currency_code,
                    payment_term_days,
                    credit_limit
                )
                VALUES (
                    :company_id,
                    :counterparty_id,
                    :number,
                    :name,
                    'sales',
                    'active',
                    :business_date,
                    'UAH',
                    0,
                    0
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "counterparty_id":
                        counterparty_id,
                    "number":
                        f"SR-CON-{token}",
                    "name":
                        (
                            "Sales Return E2E "
                            f"{token}"
                        ),
                    "business_date":
                        business_date,
                },
            )

            order_id = await insert_id(
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
                    created_by
                )
                VALUES (
                    :company_id,
                    :counterparty_id,
                    :contract_id,
                    :number,
                    'sale',
                    'order',
                    'fulfilled',
                    :business_date,
                    'UAH',
                    0,
                    :created_by
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "counterparty_id":
                        counterparty_id,
                    "contract_id":
                        contract_id,
                    "number":
                        f"SR-ORDER-{token}",
                    "business_date":
                        business_date,
                    "created_by":
                        USER_ID,
                },
            )

            order_line_id = await insert_id(
                db,
                """
                INSERT INTO trade_document_lines (
                    company_id,
                    trade_document_id,
                    line_number,
                    product_id,
                    warehouse_id,
                    quantity,
                    unit_price,
                    tax_rate_code,
                    tax_recognition_method,
                    tax_price_mode
                )
                VALUES (
                    :company_id,
                    :document_id,
                    1,
                    :product_id,
                    :warehouse_id,
                    2.0000,
                    60.00,
                    NULL,
                    NULL,
                    NULL
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "document_id":
                        order_id,
                    "product_id":
                        product_id,
                    "warehouse_id":
                        warehouse_id,
                },
            )

            invoice_id = await insert_id(
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
                    created_by
                )
                VALUES (
                    :company_id,
                    :counterparty_id,
                    :contract_id,
                    :number,
                    'sale',
                    'invoice',
                    'confirmed',
                    :business_date,
                    'UAH',
                    0,
                    :created_by
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "counterparty_id":
                        counterparty_id,
                    "contract_id":
                        contract_id,
                    "number":
                        f"SR-INVOICE-{token}",
                    "business_date":
                        business_date,
                    "created_by":
                        USER_ID,
                },
            )

            invoice_line_id = await insert_id(
                db,
                """
                INSERT INTO trade_document_lines (
                    company_id,
                    trade_document_id,
                    line_number,
                    product_id,
                    warehouse_id,
                    quantity,
                    unit_price,
                    tax_rate_code,
                    tax_recognition_method,
                    tax_price_mode
                )
                VALUES (
                    :company_id,
                    :document_id,
                    1,
                    :product_id,
                    :warehouse_id,
                    2.0000,
                    60.00,
                    NULL,
                    NULL,
                    NULL
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "document_id":
                        invoice_id,
                    "product_id":
                        product_id,
                    "warehouse_id":
                        warehouse_id,
                },
            )

            # -------------------------------------------------
            # 3. FIFO HISTORICAL SALE COST SOURCE
            # -------------------------------------------------

            source_receipt_id = await insert_id(
                db,
                """
                INSERT INTO documents (
                    company_id,
                    accounting_rule_id,
                    number,
                    document_type,
                    document_date,
                    status,
                    created_by,
                    created_at
                )
                VALUES (
                    :company_id,
                    NULL,
                    :number,
                    'receipt',
                    :business_date,
                    'posted',
                    :created_by,
                    CURRENT_TIMESTAMP
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "number":
                        f"SR-SOURCE-REC-{token}",
                    "business_date":
                        business_date,
                    "created_by":
                        USER_ID,
                },
            )

            source_receipt_line_id = await insert_id(
                db,
                """
                INSERT INTO document_lines (
                    document_id,
                    product_id,
                    warehouse_id,
                    quantity,
                    price
                )
                VALUES (
                    :document_id,
                    :product_id,
                    :warehouse_id,
                    2.0000,
                    40.0000
                )
                RETURNING id
                """,
                {
                    "document_id":
                        source_receipt_id,
                    "product_id":
                        product_id,
                    "warehouse_id":
                        warehouse_id,
                },
            )

            source_lot_id = await insert_id(
                db,
                """
                INSERT INTO stock_lots (
                    company_id,
                    product_id,
                    warehouse_id,
                    source_document_id,
                    source_document_line_id,
                    received_date,
                    original_quantity,
                    remaining_quantity,
                    unit_cost,
                    created_at
                )
                VALUES (
                    :company_id,
                    :product_id,
                    :warehouse_id,
                    :document_id,
                    :line_id,
                    :business_date,
                    2.0000,
                    0.0000,
                    40.0000,
                    CURRENT_TIMESTAMP
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "product_id":
                        product_id,
                    "warehouse_id":
                        warehouse_id,
                    "document_id":
                        source_receipt_id,
                    "line_id":
                        source_receipt_line_id,
                    "business_date":
                        business_date,
                },
            )

            issue_id = await insert_id(
                db,
                """
                INSERT INTO documents (
                    company_id,
                    accounting_rule_id,
                    number,
                    document_type,
                    document_date,
                    status,
                    created_by,
                    created_at
                )
                VALUES (
                    :company_id,
                    NULL,
                    :number,
                    'issue',
                    :business_date,
                    'posted',
                    :created_by,
                    CURRENT_TIMESTAMP
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "number":
                        f"SR-ISSUE-{token}",
                    "business_date":
                        business_date,
                    "created_by":
                        USER_ID,
                },
            )

            issue_line_id = await insert_id(
                db,
                """
                INSERT INTO document_lines (
                    document_id,
                    product_id,
                    warehouse_id,
                    quantity,
                    price
                )
                VALUES (
                    :document_id,
                    :product_id,
                    :warehouse_id,
                    2.0000,
                    0.0000
                )
                RETURNING id
                """,
                {
                    "document_id":
                        issue_id,
                    "product_id":
                        product_id,
                    "warehouse_id":
                        warehouse_id,
                },
            )

            await db.execute(
                text(
                    """
                    INSERT INTO stock_lot_consumptions (
                        company_id,
                        issue_document_id,
                        issue_document_line_id,
                        stock_lot_id,
                        quantity,
                        unit_cost,
                        created_at
                    )
                    VALUES (
                        :company_id,
                        :issue_document_id,
                        :issue_document_line_id,
                        :stock_lot_id,
                        2.0000,
                        40.0000,
                        CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "company_id":
                        COMPANY_ID,
                    "issue_document_id":
                        issue_id,
                    "issue_document_line_id":
                        issue_line_id,
                    "stock_lot_id":
                        source_lot_id,
                },
            )

            inventory_cost_entry_id = await insert_id(
                db,
                """
                INSERT INTO inventory_cost_entries (
                    company_id,
                    document_id,
                    document_line_id,
                    valuation_method,
                    quantity,
                    unit_cost,
                    valuation_amount,
                    cost_amount,
                    created_at
                )
                VALUES (
                    :company_id,
                    :document_id,
                    :document_line_id,
                    'fifo',
                    2.0000,
                    40.00000000,
                    80.00000000,
                    80.00,
                    CURRENT_TIMESTAMP
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "document_id":
                        issue_id,
                    "document_line_id":
                        issue_line_id,
                },
            )

            # -------------------------------------------------
            # 4. ORIGINAL FULFILLMENT + SALES RECOGNITION
            # -------------------------------------------------

            fulfillment_id = await insert_id(
                db,
                """
                INSERT INTO trade_fulfillments (
                    company_id,
                    trade_document_id,
                    warehouse_document_id,
                    warehouse_document_type,
                    created_by
                )
                VALUES (
                    :company_id,
                    :trade_document_id,
                    :warehouse_document_id,
                    'issue',
                    :created_by
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "trade_document_id":
                        order_id,
                    "warehouse_document_id":
                        issue_id,
                    "created_by":
                        USER_ID,
                },
            )

            fulfillment_line_id = await insert_id(
                db,
                """
                INSERT INTO trade_fulfillment_lines (
                    company_id,
                    fulfillment_id,
                    trade_document_id,
                    trade_document_line_id,
                    warehouse_document_id,
                    warehouse_document_line_id,
                    product_id,
                    warehouse_id,
                    quantity
                )
                VALUES (
                    :company_id,
                    :fulfillment_id,
                    :trade_document_id,
                    :trade_document_line_id,
                    :warehouse_document_id,
                    :warehouse_document_line_id,
                    :product_id,
                    :warehouse_id,
                    2.0000
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "fulfillment_id":
                        fulfillment_id,
                    "trade_document_id":
                        order_id,
                    "trade_document_line_id":
                        order_line_id,
                    "warehouse_document_id":
                        issue_id,
                    "warehouse_document_line_id":
                        issue_line_id,
                    "product_id":
                        product_id,
                    "warehouse_id":
                        warehouse_id,
                },
            )

            allocation_id = await insert_id(
                db,
                """
                INSERT INTO invoice_fulfillment_allocations (
                    company_id,
                    invoice_id,
                    invoice_line_id,
                    fulfillment_id,
                    fulfillment_line_id,
                    order_id,
                    order_line_id,
                    product_id,
                    quantity,
                    status,
                    created_by
                )
                VALUES (
                    :company_id,
                    :invoice_id,
                    :invoice_line_id,
                    :fulfillment_id,
                    :fulfillment_line_id,
                    :order_id,
                    :order_line_id,
                    :product_id,
                    2.0000,
                    'active',
                    :created_by
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "invoice_id":
                        invoice_id,
                    "invoice_line_id":
                        invoice_line_id,
                    "fulfillment_id":
                        fulfillment_id,
                    "fulfillment_line_id":
                        fulfillment_line_id,
                    "order_id":
                        order_id,
                    "order_line_id":
                        order_line_id,
                    "product_id":
                        product_id,
                    "created_by":
                        USER_ID,
                },
            )

            sales_recognition_event_id = await insert_id(
                db,
                """
                INSERT INTO sales_recognition_events (
                    company_id,
                    invoice_fulfillment_allocation_id,
                    recognition_date,
                    recognized_quantity,
                    recognized_gross_amount,
                    recognized_tax_amount,
                    currency_code,
                    created_by,
                    reversal_of_id
                )
                VALUES (
                    :company_id,
                    :allocation_id,
                    :business_date,
                    2.0000,
                    120.00,
                    0.00,
                    'UAH',
                    :created_by,
                    NULL
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "allocation_id":
                        allocation_id,
                    "business_date":
                        business_date,
                    "created_by":
                        USER_ID,
                },
            )

            # -------------------------------------------------
            # 5. DEDICATED RETURN RECEIPT
            #    line.price intentionally WRONG for historical
            #    cost, proving it is not authoritative.
            # -------------------------------------------------

            return_document_id = await insert_id(
                db,
                """
                INSERT INTO documents (
                    company_id,
                    accounting_rule_id,
                    number,
                    document_type,
                    document_date,
                    status,
                    created_by,
                    created_at
                )
                VALUES (
                    :company_id,
                    NULL,
                    :number,
                    'receipt',
                    :business_date,
                    'posted',
                    :created_by,
                    CURRENT_TIMESTAMP
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "number":
                        f"SR-RETURN-{token}",
                    "business_date":
                        business_date,
                    "created_by":
                        USER_ID,
                },
            )

            return_line_id = await insert_id(
                db,
                """
                INSERT INTO document_lines (
                    document_id,
                    product_id,
                    warehouse_id,
                    quantity,
                    price
                )
                VALUES (
                    :document_id,
                    :product_id,
                    :warehouse_id,
                    1.0000,
                    999.0000
                )
                RETURNING id
                """,
                {
                    "document_id":
                        return_document_id,
                    "product_id":
                        product_id,
                    "warehouse_id":
                        warehouse_id,
                },
            )

            stock_balance_before = (
                await scalar_or_none(
                    db,
                    """
                    SELECT quantity
                    FROM stock_balances
                    WHERE company_id =
                          :company_id
                      AND product_id =
                          :product_id
                      AND warehouse_id =
                          :warehouse_id
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

            if stock_balance_before is None:
                stock_balance_before = Decimal(
                    "0"
                )
            else:
                stock_balance_before = Decimal(
                    stock_balance_before
                )

            # -------------------------------------------------
            # 6. ORIGINAL TRADE RETURN EVENT
            # -------------------------------------------------

            original_return_event_id = await insert_id(
                db,
                """
                INSERT INTO trade_return_events (
                    company_id,
                    direction,
                    original_fulfillment_id,
                    original_trade_document_id,
                    original_trade_document_line_id,
                    original_fulfillment_line_id,
                    product_id,
                    return_document_id,
                    return_document_type,
                    return_document_line_id,
                    return_warehouse_id,
                    return_date,
                    returned_quantity,
                    reason_code,
                    created_by,
                    reversal_of_id
                )
                VALUES (
                    :company_id,
                    'sale',
                    :fulfillment_id,
                    :trade_document_id,
                    :trade_document_line_id,
                    :fulfillment_line_id,
                    :product_id,
                    :return_document_id,
                    'receipt',
                    :return_document_line_id,
                    :warehouse_id,
                    :business_date,
                    1.0000,
                    'E2E_RETURN',
                    :created_by,
                    NULL
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "fulfillment_id":
                        fulfillment_id,
                    "trade_document_id":
                        order_id,
                    "trade_document_line_id":
                        order_line_id,
                    "fulfillment_line_id":
                        fulfillment_line_id,
                    "product_id":
                        product_id,
                    "return_document_id":
                        return_document_id,
                    "return_document_line_id":
                        return_line_id,
                    "warehouse_id":
                        warehouse_id,
                    "business_date":
                        business_date,
                    "created_by":
                        USER_ID,
                },
            )

            original_result = (
                await apply_sales_return_operational_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_return_event_id=(
                        original_return_event_id
                    ),
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert (
                original_result.trade_return_event.id
                == original_return_event_id
            )

            assert len(
                original_result
                .economic_result
                .created_events
            ) == 1

            assert len(
                original_result
                .cost_result
                .created_events
            ) == 1

            economic_original = (
                original_result
                .economic_result
                .created_events[
                    0
                ]
            )

            cost_original = (
                original_result
                .cost_result
                .created_events[
                    0
                ]
            )

            assert (
                economic_original
                .trade_return_event_id
                == original_return_event_id
            )

            assert (
                economic_original
                .sales_recognition_event_id
                == sales_recognition_event_id
            )

            assert (
                Decimal(
                    economic_original
                    .returned_quantity
                )
                == Decimal(
                    "1.0000"
                )
            )

            assert (
                Decimal(
                    economic_original
                    .returned_gross_amount
                )
                == Decimal(
                    "60.00"
                )
            )

            assert (
                Decimal(
                    economic_original
                    .returned_tax_amount
                )
                == Decimal(
                    "0.00"
                )
            )

            assert (
                cost_original
                .trade_return_event_id
                == original_return_event_id
            )

            assert (
                cost_original
                .inventory_cost_entry_id
                == inventory_cost_entry_id
            )

            assert (
                Decimal(
                    cost_original
                    .restored_quantity
                )
                == Decimal(
                    "1.0000"
                )
            )

            assert (
                Decimal(
                    cost_original
                    .restored_valuation_amount
                )
                == Decimal(
                    "40.00000000"
                )
            )

            assert (
                Decimal(
                    cost_original
                    .restored_cost_amount
                )
                == Decimal(
                    "40.00"
                )
            )

            # Quantity runtime.
            stock_balance_after_return = Decimal(
                await scalar(
                    db,
                    """
                    SELECT quantity
                    FROM stock_balances
                    WHERE company_id =
                          :company_id
                      AND product_id =
                          :product_id
                      AND warehouse_id =
                          :warehouse_id
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

            assert (
                stock_balance_after_return
                == stock_balance_before
                + Decimal(
                    "1"
                )
            )

            return_ledger = (
                await db.execute(
                    text(
                        """
                        SELECT
                            movement_type,
                            quantity
                        FROM stock_ledger
                        WHERE company_id =
                              :company_id
                          AND document_id =
                              :document_id
                          AND document_line_id =
                              :line_id
                        ORDER BY id
                        """
                    ),
                    {
                        "company_id":
                            COMPANY_ID,
                        "document_id":
                            return_document_id,
                        "line_id":
                            return_line_id,
                    },
                )
            ).mappings().all()

            assert len(
                return_ledger
            ) == 1

            assert (
                return_ledger[
                    0
                ][
                    "movement_type"
                ]
                == "receipt"
            )

            assert (
                Decimal(
                    return_ledger[
                        0
                    ][
                        "quantity"
                    ]
                )
                == Decimal(
                    "1.0000"
                )
            )

            # FIFO physical cost restore.
            return_lot = (
                await db.execute(
                    text(
                        """
                        SELECT
                            original_quantity,
                            remaining_quantity,
                            unit_cost
                        FROM stock_lots
                        WHERE company_id =
                              :company_id
                          AND source_document_line_id =
                              :line_id
                        """
                    ),
                    {
                        "company_id":
                            COMPANY_ID,
                        "line_id":
                            return_line_id,
                    },
                )
            ).mappings().one()

            assert (
                Decimal(
                    return_lot[
                        "original_quantity"
                    ]
                )
                == Decimal(
                    "1.0000"
                )
            )

            assert (
                Decimal(
                    return_lot[
                        "remaining_quantity"
                    ]
                )
                == Decimal(
                    "1.0000"
                )
            )

            # Critical proof:
            # historical cost = 40,
            # return line.price = 999.
            assert (
                Decimal(
                    return_lot[
                        "unit_cost"
                    ]
                )
                == Decimal(
                    "40.0000"
                )
            )

            assert (
                Decimal(
                    return_lot[
                        "unit_cost"
                    ]
                )
                != Decimal(
                    "999.0000"
                )
            )

            # Economic GL: Dr704 / Cr361 60.
            economic_original_gl = (
                await journal_amounts(
                    db,
                    source_column=(
                        "sales_return_recognition_event_id"
                    ),
                    source_id=(
                        economic_original.id
                    ),
                )
            )

            assert economic_original_gl == {
                "361": (
                    Decimal(
                        "0.00"
                    ),
                    Decimal(
                        "60.00"
                    ),
                ),
                "704": (
                    Decimal(
                        "60.00"
                    ),
                    Decimal(
                        "0.00"
                    ),
                ),
            }

            # COGS restore GL: Dr281 / Cr902 40.
            cost_original_gl = (
                await journal_amounts(
                    db,
                    source_column=(
                        "sales_return_cost_restoration_event_id"
                    ),
                    source_id=(
                        cost_original.id
                    ),
                )
            )

            assert cost_original_gl == {
                "281": (
                    Decimal(
                        "40.00"
                    ),
                    Decimal(
                        "0.00"
                    ),
                ),
                "902": (
                    Decimal(
                        "0.00"
                    ),
                    Decimal(
                        "40.00"
                    ),
                ),
            }

            # Dedicated operational path must NOT create
            # generic document GL or generic return
            # InventoryCostEntry.
            assert (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM journal_entries
                    WHERE company_id =
                          :company_id
                      AND document_id =
                          :document_id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "document_id":
                            return_document_id,
                    },
                )
                == 0
            )

            assert (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM inventory_cost_entries
                    WHERE company_id =
                          :company_id
                      AND document_id =
                          :document_id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "document_id":
                            return_document_id,
                    },
                )
                == 0
            )

            # -------------------------------------------------
            # 7. ACTUAL PHYSICAL TRADE RETURN REVERSAL
            # -------------------------------------------------

            reversal_return_event_id = await insert_id(
                db,
                """
                INSERT INTO trade_return_events (
                    company_id,
                    direction,
                    original_fulfillment_id,
                    original_trade_document_id,
                    original_trade_document_line_id,
                    original_fulfillment_line_id,
                    product_id,
                    return_document_id,
                    return_document_type,
                    return_document_line_id,
                    return_warehouse_id,
                    return_date,
                    returned_quantity,
                    reason_code,
                    created_by,
                    reversal_of_id
                )
                VALUES (
                    :company_id,
                    'sale',
                    :fulfillment_id,
                    :trade_document_id,
                    :trade_document_line_id,
                    :fulfillment_line_id,
                    :product_id,
                    :return_document_id,
                    'receipt',
                    :return_document_line_id,
                    :warehouse_id,
                    :business_date,
                    1.0000,
                    'E2E_RETURN_REVERSAL',
                    :created_by,
                    :reversal_of_id
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "fulfillment_id":
                        fulfillment_id,
                    "trade_document_id":
                        order_id,
                    "trade_document_line_id":
                        order_line_id,
                    "fulfillment_line_id":
                        fulfillment_line_id,
                    "product_id":
                        product_id,
                    "return_document_id":
                        return_document_id,
                    "return_document_line_id":
                        return_line_id,
                    "warehouse_id":
                        warehouse_id,
                    "business_date":
                        business_date,
                    "created_by":
                        USER_ID,
                    "reversal_of_id":
                        original_return_event_id,
                },
            )

            reversal_result = (
                await apply_sales_return_operational_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_return_event_id=(
                        reversal_return_event_id
                    ),
                    created_by=USER_ID,
                )
            )

            await db.flush()

            assert len(
                reversal_result
                .economic_result
                .created_events
            ) == 1

            assert len(
                reversal_result
                .cost_result
                .created_events
            ) == 1

            economic_reversal = (
                reversal_result
                .economic_result
                .created_events[
                    0
                ]
            )

            cost_reversal = (
                reversal_result
                .cost_result
                .created_events[
                    0
                ]
            )

            assert (
                economic_reversal
                .reversal_of_id
                == economic_original.id
            )

            assert (
                cost_reversal
                .reversal_of_id
                == cost_original.id
            )

            # Quantity is physically back to baseline.
            stock_balance_after_reversal = Decimal(
                await scalar(
                    db,
                    """
                    SELECT quantity
                    FROM stock_balances
                    WHERE company_id =
                          :company_id
                      AND product_id =
                          :product_id
                      AND warehouse_id =
                          :warehouse_id
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

            assert (
                stock_balance_after_reversal
                == stock_balance_before
            )

            return_ledger = (
                await db.execute(
                    text(
                        """
                        SELECT
                            movement_type,
                            quantity
                        FROM stock_ledger
                        WHERE company_id =
                              :company_id
                          AND document_id =
                              :document_id
                          AND document_line_id =
                              :line_id
                        ORDER BY id
                        """
                    ),
                    {
                        "company_id":
                            COMPANY_ID,
                        "document_id":
                            return_document_id,
                        "line_id":
                            return_line_id,
                    },
                )
            ).mappings().all()

            assert [
                (
                    row[
                        "movement_type"
                    ],
                    Decimal(
                        row[
                            "quantity"
                        ]
                    ),
                )
                for row in return_ledger
            ] == [
                (
                    "receipt",
                    Decimal(
                        "1.0000"
                    ),
                ),
                (
                    "reversal",
                    Decimal(
                        "-1.0000"
                    ),
                ),
            ]

            # FIFO return lot becomes inactive.
            remaining_return_lot = Decimal(
                await scalar(
                    db,
                    """
                    SELECT remaining_quantity
                    FROM stock_lots
                    WHERE company_id =
                          :company_id
                      AND source_document_line_id =
                          :line_id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "line_id":
                            return_line_id,
                    },
                )
            )

            assert (
                remaining_return_lot
                == Decimal(
                    "0.0000"
                )
            )

            # Economic reversal GL: Dr361 / Cr704 60.
            economic_reversal_gl = (
                await journal_amounts(
                    db,
                    source_column=(
                        "sales_return_recognition_event_id"
                    ),
                    source_id=(
                        economic_reversal.id
                    ),
                )
            )

            assert economic_reversal_gl == {
                "361": (
                    Decimal(
                        "60.00"
                    ),
                    Decimal(
                        "0.00"
                    ),
                ),
                "704": (
                    Decimal(
                        "0.00"
                    ),
                    Decimal(
                        "60.00"
                    ),
                ),
            }

            # Cost reversal GL: Dr902 / Cr281 40.
            cost_reversal_gl = (
                await journal_amounts(
                    db,
                    source_column=(
                        "sales_return_cost_restoration_event_id"
                    ),
                    source_id=(
                        cost_reversal.id
                    ),
                )
            )

            assert cost_reversal_gl == {
                "281": (
                    Decimal(
                        "0.00"
                    ),
                    Decimal(
                        "40.00"
                    ),
                ),
                "902": (
                    Decimal(
                        "40.00"
                    ),
                    Decimal(
                        "0.00"
                    ),
                ),
            }

            # Typed reversal JE provenance.
            economic_reversal_je = (
                await db.execute(
                    text(
                        """
                        SELECT
                            reversal_of_id
                        FROM journal_entries
                        WHERE company_id =
                              :company_id
                          AND sales_return_recognition_event_id =
                              :source_id
                        """
                    ),
                    {
                        "company_id":
                            COMPANY_ID,
                        "source_id":
                            economic_reversal.id,
                    },
                )
            ).mappings().one()

            assert (
                economic_reversal_je[
                    "reversal_of_id"
                ]
                is not None
            )

            cost_reversal_je = (
                await db.execute(
                    text(
                        """
                        SELECT
                            reversal_of_id
                        FROM journal_entries
                        WHERE company_id =
                              :company_id
                          AND sales_return_cost_restoration_event_id =
                              :source_id
                        """
                    ),
                    {
                        "company_id":
                            COMPANY_ID,
                        "source_id":
                            cost_reversal.id,
                    },
                )
            ).mappings().one()

            assert (
                cost_reversal_je[
                    "reversal_of_id"
                ]
                is not None
            )

            # Still no generic document accounting/costing.
            assert (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM journal_entries
                    WHERE company_id =
                          :company_id
                      AND document_id =
                          :document_id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "document_id":
                            return_document_id,
                    },
                )
                == 0
            )

            assert (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM inventory_cost_entries
                    WHERE company_id =
                          :company_id
                      AND document_id =
                          :document_id
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "document_id":
                            return_document_id,
                    },
                )
                == 0
            )

            print()
            print(
                "REAL POSTGRESQL ORIGINAL RETURN:"
            )
            print(
                "  StockBalance +1"
            )
            print(
                "  StockLedger RECEIPT +1"
            )
            print(
                "  Dr704 / Cr361 = 60.00"
            )
            print(
                "  FIFO historical cost = 40.00"
            )
            print(
                "  Dr281 / Cr902 = 40.00"
            )
            print()
            print(
                "REAL POSTGRESQL RETURN REVERSAL:"
            )
            print(
                "  StockBalance back to baseline"
            )
            print(
                "  StockLedger REVERSAL -1"
            )
            print(
                "  Dr361 / Cr704 = 60.00"
            )
            print(
                "  FIFO return lot inactive"
            )
            print(
                "  Dr902 / Cr281 = 40.00"
            )
            print()
            print(
                "RETURN line.price 999.00 "
                "WAS NOT USED FOR COGS"
            )

        finally:
            # Entire fixture + operational runtime
            # MUST be transactional.
            await db.rollback()

        # -----------------------------------------------------
        # 8. REAL ROLLBACK PROOF
        # -----------------------------------------------------

        after = await table_counts(
            db
        )

        assert after == before, (
            "Sales Return PostgreSQL E2E "
            "left persistent business data\n"
            f"before={before}\n"
            f"after={after}"
        )

        marker_counterparties = int(
            await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM counterparties
                WHERE name =
                      :name
                """,
                {
                    "name":
                        (
                            "Sales Return E2E "
                            f"{token}"
                        ),
                },
            )
        )

        marker_documents = int(
            await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM documents
                WHERE company_id =
                      :company_id
                  AND number LIKE
                      :prefix
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "prefix":
                        f"SR-%-{token}",
                },
            )
        )

        marker_trade_documents = int(
            await scalar(
                db,
                """
                SELECT COUNT(*)
                FROM trade_documents
                WHERE company_id =
                      :company_id
                  AND number LIKE
                      :prefix
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "prefix":
                        f"SR-%-{token}",
                },
            )
        )

        assert marker_counterparties == 0
        assert marker_documents == 0
        assert marker_trade_documents == 0

        await db.rollback()

        print(
            "POSTGRESQL BUSINESS DATA ROLLBACK = PASS"
        )
