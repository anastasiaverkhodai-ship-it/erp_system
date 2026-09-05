import os
import re
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import (
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)
from sqlalchemy.orm import (
    selectinload,
)

from app.core.database import engine
from app.models.account import Account
from app.models.accounting_rule import (
    AccountingRule,
)
from app.models.accounting_rule_line import (
    AccountingAmountSource,
    AccountingRuleSide,
)
from app.models.document import (
    DocumentType,
)
from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)
from app.models.trade_document import (
    TradeDocument,
)
from app.models.trade_return_event import (
    TradeReturnEvent,
)
from app.services.invoice_fulfillment_allocation_service import (
    create_invoice_fulfillment_allocation,
)
from app.services.invoice_tax_calculation_service import (
    create_tax_calculations_for_invoice,
)
from app.services.purchase_return_recognition_lifecycle_service import (
    reconcile_purchase_return_recognition_lifecycle_for_fulfillment_line,
)
from app.services.purchase_return_recognition_journal_service import (
    generate_and_post_purchase_return_recognition_journal_entry,
)
from app.services.trade_fulfillment_service import (
    PurchaseOrderFulfillmentRequestLine,
    execute_purchase_order_fulfillment,
)


COMPANY_ID = 1
USER_ID = 1

ZERO = Decimal("0.00")
QTY_TWO = Decimal("2.0000")
QTY_ONE = Decimal("1.0000")

EXPECTED_RECEIPT_PRICE = Decimal("0.0150")
EXPECTED_BASE = Decimal("0.03")
EXPECTED_TAX = Decimal("0.01")
EXPECTED_GROSS = Decimal("0.04")

EXPECTED_RETURN_BASE = Decimal("0.02")
EXPECTED_RETURN_GROSS = Decimal("0.02")
EXPECTED_RETURN_TAX = Decimal("0.01")

RUN_POSTGRES_E2E = (
    os.getenv(
        "RUN_POSTGRES_E2E"
    )
    == "1"
)

pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run the real PostgreSQL "
        "Purchase Return recognition chronology test"
    ),
)


BASELINE_TABLES = (
    "counterparties",
    "trade_documents",
    "trade_document_lines",
    "counterparty_open_items",
    "tax_calculations",
    "documents",
    "document_lines",
    "trade_fulfillments",
    "trade_fulfillment_lines",
    "invoice_fulfillment_allocations",
    "trade_return_events",
    "purchase_return_recognition_events",
    "stock_lots",
    "stock_lot_consumptions",
    "inventory_cost_entries",
    "stock_balances",
    "stock_ledger",
    "journal_entries",
    "journal_entry_lines",
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


async def existing_tables():
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema =
                          current_schema()
                    """
                )
            )
        ).scalars().all()

    return set(
        rows
    )


async def table_counts():
    existing = await existing_tables()

    result = {}

    async with engine.connect() as connection:
        for table in BASELINE_TABLES:
            if table not in existing:
                continue

            if not re.fullmatch(
                r"[a-z0-9_]+",
                table,
            ):
                raise AssertionError(
                    "Unsafe table name"
                )

            result[
                table
            ] = int(
                (
                    await connection.execute(
                        text(
                            f"""
                            SELECT COUNT(*)
                            FROM {table}
                            """
                        )
                    )
                ).scalar_one()
            )

    return result


async def find_receipt_accounting_rule(
    db,
):
    """
    Resolve active RECEIPT rule by its actual accounting contract:

        Dr 281
        Cr 631
    """

    accounts = tuple(
        (
            await db.execute(
                select(
                    Account
                ).where(
                    Account.company_id
                    == COMPANY_ID,
                    Account.code.in_(
                        (
                            "281",
                            "631",
                        )
                    ),
                )
            )
        )
        .scalars()
        .all()
    )

    account_code_by_id = {
        account.id: account.code
        for account
        in accounts
    }

    if set(
        account_code_by_id.values()
    ) != {
        "281",
        "631",
    }:
        return None

    rules = tuple(
        (
            await db.execute(
                select(
                    AccountingRule
                )
                .options(
                    selectinload(
                        AccountingRule.lines
                    )
                )
                .where(
                    AccountingRule.company_id
                    == COMPANY_ID,
                    AccountingRule.document_type
                    == DocumentType.RECEIPT,
                    AccountingRule.is_active.is_(
                        True
                    ),
                )
                .order_by(
                    AccountingRule.id
                )
            )
        )
        .scalars()
        .all()
    )

    allowed_amount_sources = {
        AccountingAmountSource.LINE_TOTAL,
        AccountingAmountSource.DOCUMENT_TOTAL,
    }

    for rule in rules:
        if len(
            rule.lines
        ) != 2:
            continue

        debit_281 = tuple(
            line
            for line
            in rule.lines
            if (
                account_code_by_id.get(
                    line.account_id
                )
                == "281"
                and line.side
                == AccountingRuleSide.DEBIT
                and line.amount_source
                in allowed_amount_sources
            )
        )

        credit_631 = tuple(
            line
            for line
            in rule.lines
            if (
                account_code_by_id.get(
                    line.account_id
                )
                == "631"
                and line.side
                == AccountingRuleSide.CREDIT
                and line.amount_source
                in allowed_amount_sources
            )
        )

        if (
            len(
                debit_281
            )
            == 1
            and len(
                credit_631
            )
            == 1
        ):
            return rule.id

    return None


async def load_invoice(
    db,
    *,
    invoice_id,
):
    return (
        await db.execute(
            select(
                TradeDocument
            )
            .options(
                selectinload(
                    TradeDocument.lines
                )
            )
            .where(
                TradeDocument.company_id
                == COMPANY_ID,
                TradeDocument.id
                == invoice_id,
            )
        )
    ).scalar_one()


async def create_posted_issue(
    db,
    *,
    number,
    operation_date,
    product_id,
    warehouse_id,
):
    document_id = await insert_id(
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
            :operation_date,
            'posted',
            :created_by,
            CURRENT_TIMESTAMP
        )
        RETURNING id
        """,
        {
            "company_id": COMPANY_ID,
            "number": number,
            "operation_date":
                operation_date,
            "created_by": USER_ID,
        },
    )

    line_id = await insert_id(
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
            0.0000
        )
        RETURNING id
        """,
        {
            "document_id": document_id,
            "product_id": product_id,
            "warehouse_id":
                warehouse_id,
        },
    )

    return (
        document_id,
        line_id,
    )


async def create_purchase_return_event(
    db,
    *,
    fulfillment_id,
    fulfillment_line_id,
    order_id,
    order_line_id,
    product_id,
    warehouse_id,
    return_document_id,
    return_document_line_id,
    return_date,
    reversal_of_id=None,
):
    event = TradeReturnEvent(
        company_id=COMPANY_ID,
        direction="purchase",
        original_fulfillment_id=(
            fulfillment_id
        ),
        original_trade_document_id=(
            order_id
        ),
        original_trade_document_line_id=(
            order_line_id
        ),
        original_fulfillment_line_id=(
            fulfillment_line_id
        ),
        product_id=product_id,
        return_document_id=(
            return_document_id
        ),
        return_document_type="issue",
        return_document_line_id=(
            return_document_line_id
        ),
        return_warehouse_id=warehouse_id,
        return_date=return_date,
        returned_quantity=QTY_ONE,
        reason_code="postgres-e2e",
        created_by=USER_ID,
        reversal_of_id=reversal_of_id,
    )

    db.add(
        event
    )

    await db.flush()

    assert event.id is not None

    return event


async def load_prre_history(
    db,
    *,
    fulfillment_id,
    fulfillment_line_id,
):
    return tuple(
        (
            await db.execute(
                select(
                    PurchaseReturnRecognitionEvent
                )
                .join(
                    TradeReturnEvent,
                    (
                        TradeReturnEvent.id
                        == (
                            PurchaseReturnRecognitionEvent
                            .trade_return_event_id
                        )
                    )
                    & (
                        TradeReturnEvent.company_id
                        == (
                            PurchaseReturnRecognitionEvent
                            .company_id
                        )
                    ),
                )
                .where(
                    (
                        PurchaseReturnRecognitionEvent
                        .company_id
                        == COMPANY_ID
                    ),
                    (
                        TradeReturnEvent
                        .original_fulfillment_id
                        == fulfillment_id
                    ),
                    (
                        TradeReturnEvent
                        .original_fulfillment_line_id
                        == fulfillment_line_id
                    ),
                    (
                        TradeReturnEvent.direction
                        == "purchase"
                    ),
                )
                .order_by(
                    PurchaseReturnRecognitionEvent.id
                )
            )
        )
        .scalars()
        .all()
    )


async def purchase_return_journal_count(
    db,
    *,
    event_ids,
):
    if not event_ids:
        return 0

    return int(
        (
            await db.execute(
                select(
                    text(
                        "COUNT(*)"
                    )
                )
                .select_from(
                    text(
                        "journal_entries"
                    )
                )
                .where(
                    text(
                        "company_id = :company_id"
                    )
                )
                .where(
                    text(
                        "purchase_return_recognition_event_id "
                        "= ANY(:event_ids)"
                    )
                ),
                {
                    "company_id": COMPANY_ID,
                    "event_ids": list(
                        event_ids
                    ),
                },
            )
        ).scalar_one()
    )


async def company_journal_count(
    db,
):
    return int(
        await scalar(
            db,
            """
            SELECT COUNT(*)
            FROM journal_entries
            WHERE company_id =
                  :company_id
            """,
            {
                "company_id": COMPANY_ID,
            },
        )
    )


async def purchase_return_journal_snapshot(
    db,
    *,
    purchase_return_recognition_event_id,
):
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    je.id AS journal_entry_id,
                    je.reversal_of_id,
                    je.status,
                    je.entry_date,
                    a.code AS account_code,
                    jel.debit,
                    jel.credit
                FROM journal_entries je
                JOIN journal_entry_lines jel
                  ON jel.journal_entry_id =
                     je.id
                JOIN accounts a
                  ON a.id =
                     jel.account_id
                WHERE je.company_id =
                      :company_id
                  AND (
                        je.purchase_return_recognition_event_id
                        =
                        :event_id
                      )
                ORDER BY jel.line_no
                """
            ),
            {
                "company_id":
                    COMPANY_ID,
                "event_id":
                    purchase_return_recognition_event_id,
            },
        )
    ).mappings().all()

    if not rows:
        return None

    journal_ids = {
        int(
            row[
                "journal_entry_id"
            ]
        )
        for row in rows
    }

    if len(
        journal_ids
    ) != 1:
        raise AssertionError(
            "Purchase Return source resolved to "
            "multiple JournalEntries"
        )

    reversal_ids = {
        row[
            "reversal_of_id"
        ]
        for row in rows
    }

    statuses = {
        str(
            row[
                "status"
            ]
        )
        .split(".")[-1]
        .lower()
        for row in rows
    }

    entry_dates = {
        row[
            "entry_date"
        ]
        for row in rows
    }

    if (
        len(
            reversal_ids
        )
        != 1
        or len(
            statuses
        )
        != 1
        or len(
            entry_dates
        )
        != 1
    ):
        raise AssertionError(
            "Purchase Return JournalEntry header "
            "changed across its lines"
        )

    amounts = {}

    for row in rows:
        code = row[
            "account_code"
        ]

        if code in amounts:
            raise AssertionError(
                "Duplicate Purchase Return GL account line: "
                f"{code}"
            )

        amounts[
            code
        ] = {
            "debit":
                Decimal(
                    str(
                        row[
                            "debit"
                        ]
                    )
                ),
            "credit":
                Decimal(
                    str(
                        row[
                            "credit"
                        ]
                    )
                ),
        }

    return {
        "id":
            next(
                iter(
                    journal_ids
                )
            ),
        "reversal_of_id":
            next(
                iter(
                    reversal_ids
                )
            ),
        "status":
            next(
                iter(
                    statuses
                )
            ),
        "entry_date":
            next(
                iter(
                    entry_dates
                )
            ),
        "amounts":
            amounts,
    }


async def vat_lifecycle_snapshot(
    db,
):
    """
    Snapshot persistent tax / VAT lifecycle tables that already exist
    in this schema.

    Snapshot is taken AFTER Purchase Invoice tax + IFA setup and
    BEFORE Purchase Return recognition, so ordinary input-VAT hooks
    belonging to invoice fulfillment are not confused with Return.
    """

    names = tuple(
        (
            await db.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema =
                          current_schema()
                      AND (
                            table_name =
                                'tax_calculations'
                         OR table_name LIKE
                                'tax_recognition%'
                         OR table_name LIKE
                                'tax_credit%'
                         OR table_name LIKE
                                'input_vat%'
                         OR table_name LIKE
                                '%tax_correction%'
                         OR table_name LIKE
                                '%vat_correction%'
                      )
                    ORDER BY table_name
                    """
                )
            )
        ).scalars().all()
    )

    result = {}

    for table in names:
        if not re.fullmatch(
            r"[a-z0-9_]+",
            table,
        ):
            raise AssertionError(
                "Unsafe VAT lifecycle table name"
            )

        result[
            table
        ] = int(
            await scalar(
                db,
                f"""
                SELECT COUNT(*)
                FROM {table}
                """,
            )
        )

    return result


async def tax_row(
    db,
    *,
    invoice_id,
):
    return (
        await db.execute(
            text(
                """
                SELECT
                    direction,
                    taxable_base,
                    tax_amount,
                    currency_code
                FROM tax_calculations
                WHERE company_id =
                      :company_id
                  AND trade_document_id =
                      :invoice_id
                ORDER BY id
                """
            ),
            {
                "company_id": COMPANY_ID,
                "invoice_id": invoice_id,
            },
        )
    ).mappings().one()


@pytest.mark.asyncio
async def test_purchase_return_recognition_postgresql_chronology():
    baseline = await table_counts()

    token = uuid4().hex[
        :12
    ]

    async with engine.connect() as connection:
        transaction = (
            await connection.begin()
        )

        db = AsyncSession(
            bind=connection,
            expire_on_commit=False,
        )

        try:
            # =====================================================
            # 1. REAL ENVIRONMENT PRECONDITIONS
            # =====================================================

            company_exists = bool(
                await scalar(
                    db,
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM companies
                        WHERE id =
                              :company_id
                          AND is_active IS TRUE
                    )
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                    },
                )
            )

            assert company_exists

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
                        "user_id": USER_ID,
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

            assert business_date is not None, (
                "Real PostgreSQL E2E requires "
                "an open unlocked accounting period"
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

            accounting_rule_id = (
                await find_receipt_accounting_rule(
                    db
                )
            )

            assert (
                accounting_rule_id
                is not None
            ), (
                "Real PostgreSQL E2E requires "
                "ACTIVE Dr281/Cr631 RECEIPT rule"
            )

            # =====================================================
            # 2. PURCHASE ORDER + PURCHASE INVOICE
            #
            # Penny case:
            #
            # order / receipt:
            #   qty 2 × 0.0150 = base 0.03
            #
            # invoice VAT20 EXCLUSIVE:
            #   taxable base = 0.03
            #   VAT          = 0.01
            #   gross        = 0.04
            # =====================================================

            supplier_id = await insert_id(
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
                    'supplier',
                    'vat_payer',
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
                            "Purchase Return PG "
                            f"{token}"
                        ),
                    "short_name":
                        f"PRPG-{token}",
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
                    created_by,
                    confirmed_at
                )
                VALUES (
                    :company_id,
                    :counterparty_id,
                    NULL,
                    :number,
                    'purchase',
                    'order',
                    'confirmed',
                    :business_date,
                    'UAH',
                    0,
                    :created_by,
                    CURRENT_TIMESTAMP
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "counterparty_id":
                        supplier_id,
                    "number":
                        f"PR-PG-PO-{token}",
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
                    0.0150,
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
                    created_by,
                    confirmed_at
                )
                VALUES (
                    :company_id,
                    :counterparty_id,
                    NULL,
                    :number,
                    'purchase',
                    'invoice',
                    'confirmed',
                    :business_date,
                    'UAH',
                    0,
                    :created_by,
                    CURRENT_TIMESTAMP
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "counterparty_id":
                        supplier_id,
                    "number":
                        f"PR-PG-PI-{token}",
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
                    0.0150,
                    'VAT20',
                    'first_event',
                    'exclusive'
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

            invoice = await load_invoice(
                db,
                invoice_id=invoice_id,
            )

            calculations = (
                await create_tax_calculations_for_invoice(
                    db,
                    document=invoice,
                )
            )

            assert len(
                calculations
            ) == 1

            calculation = (
                calculations[
                    0
                ]
            )

            assert (
                str(
                    calculation.direction
                )
                in {
                    "input",
                    "TaxDirection.INPUT",
                }
            )

            assert (
                Decimal(
                    calculation.taxable_base
                )
                == EXPECTED_BASE
            )

            assert (
                Decimal(
                    calculation.tax_amount
                )
                == EXPECTED_TAX
            )

            # =====================================================
            # 2A. PURCHASE INVOICE PAYABLE OPEN ITEM
            #
            # The invoice was inserted directly as CONFIRMED.
            # Production INPUT VAT recognition therefore requires
            # the persistent payable that the normal invoice
            # confirmation lifecycle would already have created.
            #
            # Penny invoice commercial obligation:
            #     base  0.03
            #     VAT   0.01
            #     gross 0.04
            # =====================================================

            open_item_id = await insert_id(
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
                VALUES (
                    :company_id,
                    :invoice_id,
                    :counterparty_id,
                    NULL,
                    'payable',
                    'open',
                    :document_date,
                    :due_date,
                    'UAH',
                    :original_amount
                )
                RETURNING id
                """,
                {
                    "company_id":
                        COMPANY_ID,
                    "invoice_id":
                        invoice_id,
                    "counterparty_id":
                        supplier_id,
                    "document_date":
                        business_date,
                    "due_date":
                        business_date,
                    "original_amount":
                        EXPECTED_GROSS,
                },
            )

            assert open_item_id > 0

            stored_open_item = (
                await db.execute(
                    text(
                        """
                        SELECT
                            item_type,
                            status,
                            currency_code,
                            original_amount
                        FROM counterparty_open_items
                        WHERE company_id =
                              :company_id
                          AND id =
                              :open_item_id
                        """
                    ),
                    {
                        "company_id":
                            COMPANY_ID,
                        "open_item_id":
                            open_item_id,
                    },
                )
            ).mappings().one()

            assert (
                stored_open_item[
                    "item_type"
                ]
                == "payable"
            )

            assert (
                stored_open_item[
                    "status"
                ]
                == "open"
            )

            assert (
                stored_open_item[
                    "currency_code"
                ]
                == "UAH"
            )

            assert (
                Decimal(
                    stored_open_item[
                        "original_amount"
                    ]
                )
                == EXPECTED_GROSS
            )

            print(
                "PURCHASE INVOICE PAYABLE OPEN ITEM: "
                "0.04 = PASS"
            )

            # =====================================================
            # 3. REAL POSTED PURCHASE RECEIPT
            #
            # Production service:
            #   Dr281 / Cr631 = 0.03
            # =====================================================

            receipt = (
                await execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=order_id,
                    warehouse_document_number=(
                        f"PR-PG-REC-{token}"
                    ),
                    document_date=business_date,
                    accounting_rule_id=(
                        accounting_rule_id
                    ),
                    created_by=USER_ID,
                    request_lines=(
                        PurchaseOrderFulfillmentRequestLine(
                            trade_document_line_id=(
                                order_line_id
                            ),
                            quantity=QTY_TWO,
                        ),
                    ),
                )
            )

            fulfillment_id = int(
                receipt.fulfillment.id
            )

            fulfillment_line = (
                await db.execute(
                    text(
                        """
                        SELECT
                            id,
                            warehouse_document_id,
                            warehouse_document_line_id,
                            quantity
                        FROM trade_fulfillment_lines
                        WHERE company_id =
                              :company_id
                          AND fulfillment_id =
                              :fulfillment_id
                        ORDER BY id
                        LIMIT 1
                        """
                    ),
                    {
                        "company_id":
                            COMPANY_ID,
                        "fulfillment_id":
                            fulfillment_id,
                    },
                )
            ).mappings().one()

            fulfillment_line_id = int(
                fulfillment_line[
                    "id"
                ]
            )

            receipt_line = (
                await db.execute(
                    text(
                        """
                        SELECT
                            quantity,
                            price
                        FROM document_lines
                        WHERE id =
                              :line_id
                        """
                    ),
                    {
                        "line_id":
                            fulfillment_line[
                                "warehouse_document_line_id"
                            ],
                    },
                )
            ).mappings().one()

            assert (
                Decimal(
                    receipt_line[
                        "quantity"
                    ]
                )
                == QTY_TWO
            )

            assert (
                Decimal(
                    receipt_line[
                        "price"
                    ]
                )
                == EXPECTED_RECEIPT_PRICE
            )

            # =====================================================
            # 4. REAL ACTIVE IFA
            # =====================================================

            allocation = (
                await create_invoice_fulfillment_allocation(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=invoice_id,
                    invoice_line_id=(
                        invoice_line_id
                    ),
                    fulfillment_id=(
                        fulfillment_id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    quantity=QTY_TWO,
                    created_by=USER_ID,
                )
            )

            assert allocation.id is not None

            tax_before_return = (
                await tax_row(
                    db,
                    invoice_id=invoice_id,
                )
            )

            assert (
                Decimal(
                    tax_before_return[
                        "taxable_base"
                    ]
                )
                == EXPECTED_BASE
            )

            assert (
                Decimal(
                    tax_before_return[
                        "tax_amount"
                    ]
                )
                == EXPECTED_TAX
            )

            journal_count_before_return = (
                await company_journal_count(
                    db
                )
            )

            vat_before_return = (
                await vat_lifecycle_snapshot(
                    db
                )
            )

            # =====================================================
            # 5. ORIGINAL PURCHASE RETURN QTY 1
            # =====================================================

            original_return_date = (
                business_date
                + timedelta(
                    days=1
                )
            )

            (
                return_document_id,
                return_document_line_id,
            ) = await create_posted_issue(
                db,
                number=(
                    f"PR-PG-ISSUE-1-{token}"
                ),
                operation_date=(
                    original_return_date
                ),
                product_id=product_id,
                warehouse_id=warehouse_id,
            )

            original_return = (
                await create_purchase_return_event(
                    db,
                    fulfillment_id=(
                        fulfillment_id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    order_id=order_id,
                    order_line_id=(
                        order_line_id
                    ),
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                    return_document_id=(
                        return_document_id
                    ),
                    return_document_line_id=(
                        return_document_line_id
                    ),
                    return_date=(
                        original_return_date
                    ),
                )
            )

            result_original = (
                await reconcile_purchase_return_recognition_lifecycle_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_id=(
                        fulfillment_id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=original_return_date,
                    created_by=USER_ID,
                )
            )

            assert len(
                result_original.desired_targets
            ) == 1

            assert len(
                result_original.created_events
            ) == 1

            history = await load_prre_history(
                db,
                fulfillment_id=(
                    fulfillment_id
                ),
                fulfillment_line_id=(
                    fulfillment_line_id
                ),
            )

            assert len(
                history
            ) == 1

            original_prre = history[
                0
            ]

            assert (
                original_prre.reversal_of_id
                is None
            )

            assert (
                original_prre
                .trade_return_event_id
                == original_return.id
            )

            assert (
                original_prre
                .invoice_fulfillment_allocation_id
                == allocation.id
            )

            assert (
                Decimal(
                    original_prre
                    .returned_quantity
                )
                == QTY_ONE
            )

            assert (
                Decimal(
                    original_prre
                    .returned_base_amount
                )
                == EXPECTED_RETURN_BASE
            )

            assert (
                Decimal(
                    original_prre
                    .returned_gross_amount
                )
                == EXPECTED_RETURN_GROSS
            )

            assert (
                Decimal(
                    original_prre
                    .returned_tax_amount
                )
                == EXPECTED_RETURN_TAX
            )

            assert (
                Decimal(
                    original_prre
                    .returned_gross_amount
                )
                - Decimal(
                    original_prre
                    .returned_tax_amount
                )
                == Decimal("0.01")
            )

            assert (
                Decimal(
                    original_prre
                    .returned_base_amount
                )
                != (
                    Decimal(
                        original_prre
                        .returned_gross_amount
                    )
                    - Decimal(
                        original_prre
                        .returned_tax_amount
                    )
                )
            )

            print(
                "REAL POSTGRESQL ORIGINAL PURCHASE RETURN: "
                "base=0.02 gross=0.02 tax=0.01 = PASS"
            )

            print(
                "REAL POSTGRESQL PENNY ROUNDING: "
                "base 0.02 != gross-tax 0.01 = PASS"
            )

            # =====================================================
            # 6. RETURN REVERSAL
            #
            # Original TradeReturnEvent becomes inactive.
            # Existing PRRE is NOT updated/deleted:
            # immutable PRRE reversal is appended.
            # =====================================================

            reversal_date = (
                business_date
                + timedelta(
                    days=2
                )
            )

            #
            # TradeReturnEvent reversal is an immutable reversal of
            # the SAME physical return source.
            #
            # The composite reversal FK deliberately preserves:
            #   direction
            #   original fulfillment/order provenance
            #   product
            #   return document
            #   return document type
            #   return document line
            #   return warehouse
            #
            # Therefore the reversal MUST copy the original physical
            # return document provenance. A new ISSUE document would
            # describe a different physical return and is invalid.
            #

            reversal_return = (
                await create_purchase_return_event(
                    db,
                    fulfillment_id=(
                        fulfillment_id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    order_id=order_id,
                    order_line_id=(
                        order_line_id
                    ),
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                    return_document_id=(
                        return_document_id
                    ),
                    return_document_line_id=(
                        return_document_line_id
                    ),
                    return_date=reversal_date,
                    reversal_of_id=(
                        original_return.id
                    ),
                )
            )

            assert (
                reversal_return.reversal_of_id
                == original_return.id
            )

            result_reversal = (
                await reconcile_purchase_return_recognition_lifecycle_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_id=(
                        fulfillment_id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    created_by=USER_ID,
                    adjustment_date=(
                        reversal_date
                    ),
                )
            )

            assert (
                result_reversal.desired_targets
                == ()
            )

            history = await load_prre_history(
                db,
                fulfillment_id=(
                    fulfillment_id
                ),
                fulfillment_line_id=(
                    fulfillment_line_id
                ),
            )

            assert len(
                history
            ) == 2

            assert (
                history[
                    0
                ].id
                == original_prre.id
            )

            assert (
                history[
                    0
                ].reversal_of_id
                is None
            )

            prre_reversal = history[
                1
            ]

            assert (
                prre_reversal.reversal_of_id
                == original_prre.id
            )

            assert (
                prre_reversal
                .trade_return_event_id
                == original_return.id
            )

            assert (
                prre_reversal
                .invoice_fulfillment_allocation_id
                == allocation.id
            )

            assert (
                Decimal(
                    prre_reversal
                    .returned_base_amount
                )
                == EXPECTED_RETURN_BASE
            )

            assert (
                Decimal(
                    prre_reversal
                    .returned_gross_amount
                )
                == EXPECTED_RETURN_GROSS
            )

            assert (
                Decimal(
                    prre_reversal
                    .returned_tax_amount
                )
                == EXPECTED_RETURN_TAX
            )

            print(
                "REAL POSTGRESQL PURCHASE RETURN REVERSAL: "
                "immutable PRRE reversal = PASS"
            )

            # =====================================================
            # 7. REPLACEMENT RETURN
            #
            # New immutable physical return -> new immutable PRRE.
            # Historical original + reversal remain untouched.
            # =====================================================

            replacement_date = (
                business_date
                + timedelta(
                    days=3
                )
            )

            (
                replacement_document_id,
                replacement_document_line_id,
            ) = await create_posted_issue(
                db,
                number=(
                    f"PR-PG-ISSUE-REP-{token}"
                ),
                operation_date=(
                    replacement_date
                ),
                product_id=product_id,
                warehouse_id=warehouse_id,
            )

            replacement_return = (
                await create_purchase_return_event(
                    db,
                    fulfillment_id=(
                        fulfillment_id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    order_id=order_id,
                    order_line_id=(
                        order_line_id
                    ),
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                    return_document_id=(
                        replacement_document_id
                    ),
                    return_document_line_id=(
                        replacement_document_line_id
                    ),
                    return_date=(
                        replacement_date
                    ),
                )
            )

            result_replacement = (
                await reconcile_purchase_return_recognition_lifecycle_for_fulfillment_line(
                    db,
                    company_id=COMPANY_ID,
                    fulfillment_id=(
                        fulfillment_id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    adjustment_date=replacement_date,
                    created_by=USER_ID,
                )
            )

            assert len(
                result_replacement.desired_targets
            ) == 1

            history = await load_prre_history(
                db,
                fulfillment_id=(
                    fulfillment_id
                ),
                fulfillment_line_id=(
                    fulfillment_line_id
                ),
            )

            assert len(
                history
            ) == 3

            assert (
                history[
                    0
                ].id
                == original_prre.id
            )

            assert (
                history[
                    1
                ].id
                == prre_reversal.id
            )

            replacement_prre = history[
                2
            ]

            assert (
                replacement_prre.reversal_of_id
                is None
            )

            assert (
                replacement_prre
                .trade_return_event_id
                == replacement_return.id
            )

            assert (
                replacement_prre
                .invoice_fulfillment_allocation_id
                == allocation.id
            )

            assert (
                Decimal(
                    replacement_prre
                    .returned_base_amount
                )
                == EXPECTED_RETURN_BASE
            )

            assert (
                Decimal(
                    replacement_prre
                    .returned_gross_amount
                )
                == EXPECTED_RETURN_GROSS
            )

            assert (
                Decimal(
                    replacement_prre
                    .returned_tax_amount
                )
                == EXPECTED_RETURN_TAX
            )

            active_originals = tuple(
                event
                for event
                in history
                if (
                    event.reversal_of_id
                    is None
                    and not any(
                        other.reversal_of_id
                        == event.id
                        for other
                        in history
                    )
                )
            )

            assert active_originals == (
                replacement_prre,
            )

            print(
                "REAL POSTGRESQL PURCHASE RETURN REPLACEMENT: "
                "immutable replacement = PASS"
            )

            # =====================================================
            # 8. PURCHASE RETURN ACCOUNTING JE LIFECYCLE
            #
            # Economic Purchase Return accounting uses ONLY:
            #
            #     returned_base_amount
            #
            # Original:
            #     Dr 631
            #     Cr 281
            #
            # Reversal:
            #     Dr 281
            #     Cr 631
            #
            # Replacement:
            #     Dr 631
            #     Cr 281
            #
            # VAT / RK state must remain unchanged.
            # =====================================================

            prre_ids = tuple(
                event.id
                for event
                in history
            )

            assert len(
                prre_ids
            ) == 3

            assert (
                await purchase_return_journal_count(
                    db,
                    event_ids=prre_ids,
                )
                == 3
            )

            original_je = (
                await purchase_return_journal_snapshot(
                    db,
                    purchase_return_recognition_event_id=(
                        original_prre.id
                    ),
                )
            )

            reversal_je = (
                await purchase_return_journal_snapshot(
                    db,
                    purchase_return_recognition_event_id=(
                        prre_reversal.id
                    ),
                )
            )

            replacement_je = (
                await purchase_return_journal_snapshot(
                    db,
                    purchase_return_recognition_event_id=(
                        replacement_prre.id
                    ),
                )
            )

            assert original_je is not None
            assert reversal_je is not None
            assert replacement_je is not None

            # -------------------------------------------------
            # Original PRRE:
            #
            #     Dr 631 0.02
            #     Cr 281 0.02
            # -------------------------------------------------

            assert (
                original_je[
                    "reversal_of_id"
                ]
                is None
            )

            # Generic immutable reversal marks the original
            # JournalEntry as REVERSED. The separate reversal
            # JournalEntry remains POSTED.
            assert (
                original_je[
                    "status"
                ]
                == "reversed"
            )

            assert (
                original_je[
                    "entry_date"
                ]
                == original_return_date
            )

            assert (
                original_je[
                    "amounts"
                ]
                == {
                    "631": {
                        "debit":
                            EXPECTED_RETURN_BASE,
                        "credit":
                            ZERO,
                    },
                    "281": {
                        "debit":
                            ZERO,
                        "credit":
                            EXPECTED_RETURN_BASE,
                    },
                }
            )

            # -------------------------------------------------
            # Immutable PRRE reversal:
            #
            #     Dr 281 0.02
            #     Cr 631 0.02
            # -------------------------------------------------

            assert (
                reversal_je[
                    "reversal_of_id"
                ]
                == original_je[
                    "id"
                ]
            )

            assert (
                reversal_je[
                    "status"
                ]
                == "posted"
            )

            assert (
                reversal_je[
                    "entry_date"
                ]
                == reversal_date
            )

            assert (
                reversal_je[
                    "amounts"
                ]
                == {
                    "631": {
                        "debit":
                            ZERO,
                        "credit":
                            EXPECTED_RETURN_BASE,
                    },
                    "281": {
                        "debit":
                            EXPECTED_RETURN_BASE,
                        "credit":
                            ZERO,
                    },
                }
            )

            # -------------------------------------------------
            # Replacement PRRE:
            #
            #     Dr 631 0.02
            #     Cr 281 0.02
            # -------------------------------------------------

            assert (
                replacement_je[
                    "reversal_of_id"
                ]
                is None
            )

            assert (
                replacement_je[
                    "status"
                ]
                == "posted"
            )

            assert (
                replacement_je[
                    "entry_date"
                ]
                == replacement_date
            )

            assert (
                replacement_je[
                    "amounts"
                ]
                == {
                    "631": {
                        "debit":
                            EXPECTED_RETURN_BASE,
                        "credit":
                            ZERO,
                    },
                    "281": {
                        "debit":
                            ZERO,
                        "credit":
                            EXPECTED_RETURN_BASE,
                    },
                }
            )

            # Original + reversal cancel.
            # Replacement remains as current active economic return.

            assert (
                await company_journal_count(
                    db
                )
                == (
                    journal_count_before_return
                    + 3
                )
            )

            # No 641 / 644 or tax-event side effect is allowed
            # at this Purchase Return economic accounting milestone.

            assert (
                await vat_lifecycle_snapshot(
                    db
                )
                == vat_before_return
            )

            tax_after_return = (
                await tax_row(
                    db,
                    invoice_id=invoice_id,
                )
            )

            assert (
                tax_after_return
                == tax_before_return
            )

            print(
                "REAL POSTGRESQL PURCHASE RETURN ORIGINAL JE: "
                "Dr631 / Cr281 = 0.02 = PASS"
            )

            print(
                "REAL POSTGRESQL PURCHASE RETURN REVERSAL JE: "
                "Dr281 / Cr631 = 0.02 = PASS"
            )

            print(
                "REAL POSTGRESQL PURCHASE RETURN REPLACEMENT JE: "
                "Dr631 / Cr281 = 0.02 = PASS"
            )

            print(
                "PURCHASE RETURN JE SOURCE TYPING + "
                "IMMUTABLE REVERSAL = PASS"
            )

            print(
                "PURCHASE RETURN VAT/RK LIFECYCLE: "
                "UNCHANGED = PASS"
            )

            # =====================================================
            # 9. ZERO-BASE PURCHASE RETURN ACCOUNTING BOUNDARY
            #
            # A legitimate immutable PurchaseReturnRecognitionEvent
            # may have:
            #
            #     returned_quantity > 0
            #     returned_base_amount = 0.00
            #
            # because VAT-exclusive historical base is independently
            # cumulative-rounded.
            #
            # Such an economic fact MUST remain persisted, but it must
            # NOT create a zero-value JournalEntry.
            #
            # Commercial gross/tax snapshots remain independent and
            # positive here specifically to prove that GL does not use:
            #
            #     gross - tax
            #
            # =====================================================

            journal_count_before_zero = (
                await company_journal_count(
                    db
                )
            )

            vat_before_zero = (
                await vat_lifecycle_snapshot(
                    db
                )
            )

            (
                zero_issue_id,
                zero_issue_line_id,
            ) = await create_posted_issue(
                db,
                number=(
                    f"PR-PG-ISSUE-ZERO-{token}"
                ),
                operation_date=(
                    replacement_date
                ),
                product_id=product_id,
                warehouse_id=warehouse_id,
            )

            zero_return = (
                await create_purchase_return_event(
                    db,
                    fulfillment_id=(
                        fulfillment_id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_id
                    ),
                    order_id=order_id,
                    order_line_id=(
                        order_line_id
                    ),
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                    return_document_id=(
                        zero_issue_id
                    ),
                    return_document_line_id=(
                        zero_issue_line_id
                    ),
                    return_date=(
                        replacement_date
                    ),
                )
            )

            zero_prre = (
                PurchaseReturnRecognitionEvent(
                    company_id=COMPANY_ID,
                    trade_return_event_id=(
                        zero_return.id
                    ),
                    invoice_fulfillment_allocation_id=(
                        replacement_prre
                        .invoice_fulfillment_allocation_id
                    ),
                    recognition_date=(
                        replacement_date
                    ),
                    returned_quantity=(
                        Decimal("1.0000")
                    ),
                    returned_base_amount=(
                        Decimal("0.00")
                    ),
                    returned_gross_amount=(
                        EXPECTED_RETURN_GROSS
                    ),
                    returned_tax_amount=(
                        EXPECTED_RETURN_TAX
                    ),
                    currency_code="UAH",
                    created_by=USER_ID,
                    reversal_of_id=None,
                )
            )

            db.add(
                zero_prre
            )

            await db.flush()

            assert zero_prre.id is not None
            assert zero_prre.id > 0

            assert (
                Decimal(
                    zero_prre.returned_base_amount
                )
                == Decimal("0.00")
            )

            assert (
                Decimal(
                    zero_prre.returned_quantity
                )
                > Decimal("0")
            )

            # Deliberately positive commercial snapshot.
            #
            # If GL accidentally derived base as gross-tax,
            # this event would incorrectly produce 0.01.
            assert (
                Decimal(
                    zero_prre.returned_gross_amount
                )
                == EXPECTED_RETURN_GROSS
            )

            assert (
                Decimal(
                    zero_prre.returned_tax_amount
                )
                == EXPECTED_RETURN_TAX
            )

            assert (
                Decimal(
                    zero_prre.returned_gross_amount
                )
                - Decimal(
                    zero_prre.returned_tax_amount
                )
                != Decimal(
                    zero_prre.returned_base_amount
                )
            )

            zero_journal_result = (
                await generate_and_post_purchase_return_recognition_journal_entry(
                    db,
                    event=zero_prre,
                    created_by=USER_ID,
                )
            )

            assert zero_journal_result is None

            assert (
                await purchase_return_journal_snapshot(
                    db,
                    purchase_return_recognition_event_id=(
                        zero_prre.id
                    ),
                )
                is None
            )

            assert (
                await purchase_return_journal_count(
                    db,
                    event_ids=(
                        zero_prre.id,
                    ),
                )
                == 0
            )

            assert (
                await company_journal_count(
                    db
                )
                == journal_count_before_zero
            )

            assert (
                await vat_lifecycle_snapshot(
                    db
                )
                == vat_before_zero
            )

            persisted_zero = (
                await db.execute(
                    select(
                        PurchaseReturnRecognitionEvent
                    ).where(
                        (
                            PurchaseReturnRecognitionEvent
                            .company_id
                            == COMPANY_ID
                        ),
                        (
                            PurchaseReturnRecognitionEvent
                            .id
                            == zero_prre.id
                        ),
                    )
                )
            ).scalar_one()

            assert (
                Decimal(
                    persisted_zero
                    .returned_base_amount
                )
                == Decimal("0.00")
            )

            assert (
                Decimal(
                    persisted_zero
                    .returned_quantity
                )
                == Decimal("1.0000")
            )

            print(
                "REAL POSTGRESQL ZERO-BASE PURCHASE RETURN: "
                "PRRE EXISTS / JOURNAL ABSENT = PASS"
            )

            print(
                "ZERO-BASE GL DERIVATION GUARD: "
                "gross-tax DOES NOT DRIVE JE = PASS"
            )

            print(
                "ZERO-BASE PURCHASE RETURN VAT/RK: "
                "UNCHANGED = PASS"
            )

        finally:
            await db.close()

            if transaction.is_active:
                await transaction.rollback()

    after = await table_counts()

    assert after == baseline, (
        "\nPostgreSQL E2E rollback did not restore "
        "business data.\n"
        f"before={baseline}\n"
        f"after={after}"
    )

    print(
        "POSTGRESQL BUSINESS DATA ROLLBACK = PASS"
    )
