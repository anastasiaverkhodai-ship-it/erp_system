import calendar
import os
import sys
import uuid
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal

import pytest
from sqlalchemy import (
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.core.database import engine
from app.models.trade_value_correction_event import (
    TradeValueCorrectionEvent,
)
from app.services.invoice_fulfillment_allocation_service import (
    create_invoice_fulfillment_allocation,
    reverse_invoice_fulfillment_allocation,
)
from app.services.purchase_value_correction_allocation_reconciliation_service import (
    reconcile_purchase_value_correction_allocations_for_event,
)
from app.services.trade_fulfillment_service import (
    PurchaseOrderFulfillmentRequestLine,
    execute_purchase_order_fulfillment,
)


RUN_POSTGRES_E2E = (
    os.getenv(
        "RUN_POSTGRES_E2E"
    )
    == "1"
)

COMPANY_ID = 1
USER_ID = 1
MONEY_ZERO = Decimal("0.00")

BASELINE_TABLES = (
    "accounting_periods",
    "counterparties",
    "trade_documents",
    "trade_document_lines",
    "counterparty_open_items",
    "payments",
    "payment_settlement_allocations",
    "documents",
    "document_lines",
    "trade_fulfillments",
    "trade_fulfillment_lines",
    "invoice_fulfillment_allocations",
    "tax_calculations",
    "input_vat_fulfillment_bridge_events",
    "supplier_advance_clearing_events",
    "journal_entries",
    "journal_entry_lines",
    "stock_ledger",
    "stock_balances",
    "stock_lots",
    "inventory_cost_entries",
    "moving_average_balances",
    "moving_average_movements",
    "trade_value_correction_events",
    "purchase_value_correction_allocation_events",
)



async def scalar(
    db,
    sql,
    params=None,
):
    result = await db.execute(
        text(
            sql
        ),
        (
            params
            or {}
        ),
    )

    return result.scalar_one()


async def scalar_or_none(
    db,
    sql,
    params=None,
):
    result = await db.execute(
        text(
            sql
        ),
        (
            params
            or {}
        ),
    )

    return result.scalar_one_or_none()


async def table_counts():
    values = {}

    async with engine.connect() as connection:
        for table_name in BASELINE_TABLES:
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


async def find_receipt_accounting_rule(
    db,
):
    """
    Resolve an ACTIVE company receipt accounting rule
    directly from its rule-line contract.

    Required warehouse receipt posting:

        Dr 281
        Cr 631

    This E2E must not depend on a historical posted
    receipt already existing in PostgreSQL.
    """

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.account import Account
    from app.models.accounting_rule import AccountingRule
    from app.models.accounting_rule_line import (
        AccountingAmountSource,
        AccountingRuleSide,
    )
    from app.models.document import DocumentType

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
        for account in accounts
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
            for line in rule.lines
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
            for line in rule.lines
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
            print(
                "E2E RECEIPT ACCOUNTING RULE: "
                f"id={rule.id} "
                "Dr281 / Cr631 = PASS"
            )

            return rule.id

    return None


async def create_business_fixture(
    db,
):
    suffix = (
        uuid.uuid4()
        .hex[
            :12
        ]
    )

    now = datetime.now(
        timezone.utc
    )

    business_date = (
        now.date()
    )

    # ------------------------------------------------------
    # PostgreSQL chronology must exercise production
    # accounting-period guards for BOTH:
    #
    #   - original postings
    #   - reversal postings
    #
    # Reversal services deliberately use the actual
    # reversal date (allocation.reversed_at.date()).
    #
    # Therefore this integration fixture guarantees that
    # business_date / today's reversal date belongs to an
    # OPEN + unlocked period.
    #
    # Any period created here is transaction-local and is
    # removed by the final E2E rollback.
    # ------------------------------------------------------

    existing_period = (
        await db.execute(
            text(
                """
                SELECT
                    id,
                    status,
                    is_locked,
                    start_date,
                    end_date
                FROM accounting_periods
                WHERE company_id =
                      :company_id
                  AND start_date <=
                      :business_date
                  AND end_date >=
                      :business_date
                ORDER BY id
                LIMIT 1
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
                "business_date": (
                    business_date
                ),
            },
        )
    ).mappings().one_or_none()

    if existing_period is None:
        from calendar import monthrange

        month_start = (
            business_date.replace(
                day=1
            )
        )

        month_end = (
            business_date.replace(
                day=monthrange(
                    business_date.year,
                    business_date.month,
                )[
                    1
                ]
            )
        )

        period_id = await scalar(
            db,
            """
            INSERT INTO accounting_periods (
                company_id,
                year,
                month,
                start_date,
                end_date,
                status,
                is_locked,
                created_at
            )
            VALUES (
                :company_id,
                :year,
                :month,
                :start_date,
                :end_date,
                'open',
                FALSE,
                CURRENT_TIMESTAMP
            )
            RETURNING id
            """,
            {
                "company_id": (
                    COMPANY_ID
                ),
                "year": (
                    business_date.year
                ),
                "month": (
                    business_date.month
                ),
                "start_date": (
                    month_start
                ),
                "end_date": (
                    month_end
                ),
            },
        )

        print(
            "E2E CURRENT ACCOUNTING PERIOD: "
            f"created id={period_id} "
            f"{month_start}..{month_end} "
            "= PASS"
        )

    else:
        status_value = str(
            existing_period[
                "status"
            ]
        ).lower()

        assert (
            status_value
            == "open"
        ), (
            "Current E2E accounting period "
            "exists but is not OPEN"
        )

        assert (
            existing_period[
                "is_locked"
            ]
            is False
        ), (
            "Current E2E accounting period "
            "exists but is locked"
        )

        print(
            "E2E CURRENT ACCOUNTING PERIOD: "
            f"existing id="
            f"{existing_period['id']} "
            f"{existing_period['start_date']}"
            ".."
            f"{existing_period['end_date']} "
            "= PASS"
        )

    period_guard_count = await scalar(
        db,
        """
        SELECT COUNT(*)
        FROM accounting_periods
        WHERE company_id =
              :company_id
          AND start_date <=
              :business_date
          AND end_date >=
              :business_date
          AND status =
              'open'
          AND is_locked IS FALSE
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "business_date": (
                business_date
            ),
        },
    )

    assert (
        period_guard_count
        == 1
    )

    print(
        "E2E BUSINESS / REVERSAL DATE: "
        f"{business_date} "
        "OPEN PERIOD = PASS"
    )

    company_ok = await scalar(
        db,
        """
        SELECT COUNT(*)
        FROM companies
        WHERE id = :company_id
          AND is_active IS TRUE
          AND chart_of_accounts_template =
              'general_291'
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
        },
    )

    assert company_ok == 1

    user_ok = await scalar(
        db,
        """
        SELECT COUNT(*)
        FROM users
        WHERE id = :user_id
          AND is_active IS TRUE
        """,
        {
            "user_id": USER_ID,
        },
    )

    assert user_ok == 1

    account_rows = (
        await db.execute(
            text(
                """
                SELECT
                    code,
                    is_system,
                    is_active,
                    is_postable
                FROM accounts
                WHERE company_id =
                      :company_id
                  AND code IN (
                      '281',
                      '311',
                      '371',
                      '631'
                  )
                ORDER BY code
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
            },
        )
    ).mappings().all()

    assert {
        row[
            "code"
        ]
        for row
        in account_rows
    } == {
        "281",
        "311",
        "371",
        "631",
    }

    for row in account_rows:
        assert (
            row[
                "is_system"
            ]
            is True
        )

        assert (
            row[
                "is_active"
            ]
            is True
        )

        assert (
            row[
                "is_postable"
            ]
            is True
        )

    product_id = await scalar(
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
            "company_id": (
                COMPANY_ID
            ),
        },
    )

    warehouse_id = await scalar(
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
            "company_id": (
                COMPANY_ID
            ),
        },
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
        "No existing company-1 POSTED "
        "Dr281/Cr631 receipt accounting rule "
        "could be found"
    )

    supplier_id = await scalar(
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
                "Supplier E2E "
                + suffix
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
            'purchase',
            'order',
            'confirmed',
            :document_date,
            'UAH',
            0,
            :created_by,
            :confirmed_at
        )
        RETURNING id
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "counterparty_id": (
                supplier_id
            ),
            "number": (
                "PO-E2E-"
                + suffix
            ),
            "document_date": (
                business_date
            ),
            "created_by": USER_ID,
            "confirmed_at": now,
        },
    )

    order_line_id = await scalar(
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
            120.0000,
            1.0000
        )
        RETURNING id
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "document_id": order_id,
            "product_id": product_id,
            "warehouse_id": (
                warehouse_id
            ),
        },
    )

    invoice_id = await scalar(
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
            :document_date,
            'UAH',
            0,
            :created_by,
            :confirmed_at
        )
        RETURNING id
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "counterparty_id": (
                supplier_id
            ),
            "number": (
                "PI-E2E-"
                + suffix
            ),
            "document_date": (
                business_date
            ),
            "created_by": USER_ID,
            "confirmed_at": now,
        },
    )

    invoice_line_id = await scalar(
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
            120.0000,
            1.0000
        )
        RETURNING id
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "document_id": (
                invoice_id
            ),
            "product_id": product_id,
            "warehouse_id": (
                warehouse_id
            ),
        },
    )

    open_item_id = await scalar(
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
            120.00
        )
        RETURNING id
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "invoice_id": (
                invoice_id
            ),
            "counterparty_id": (
                supplier_id
            ),
            "document_date": (
                business_date
            ),
            "due_date": (
                business_date
            ),
        },
    )

    payment_id = await scalar(
        db,
        """
        INSERT INTO payments (
            company_id,
            counterparty_id,
            contract_id,
            number,
            direction,
            status,
            payment_date,
            currency_code,
            amount,
            created_by
        )
        VALUES (
            :company_id,
            :counterparty_id,
            NULL,
            :number,
            'outgoing',
            'draft',
            :payment_date,
            'UAH',
            120.00,
            :created_by
        )
        RETURNING id
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "counterparty_id": (
                supplier_id
            ),
            "number": (
                "PAY-E2E-"
                + suffix
            ),
            "payment_date": (
                business_date
            ),
            "created_by": USER_ID,
        },
    )

    return {
        "suffix": suffix,
        "business_date": (
            business_date
        ),
        "product_id": product_id,
        "warehouse_id": (
            warehouse_id
        ),
        "accounting_rule_id": (
            accounting_rule_id
        ),
        "supplier_id": supplier_id,
        "order_id": order_id,
        "order_line_id": (
            order_line_id
        ),
        "invoice_id": invoice_id,
        "invoice_line_id": (
            invoice_line_id
        ),
        "open_item_id": (
            open_item_id
        ),
        "payment_id": payment_id,
    }


async def fulfillment_line_id(
    db,
    *,
    fulfillment_id,
):
    return await scalar(
        db,
        """
        SELECT id
        FROM trade_fulfillment_lines
        WHERE company_id =
              :company_id
          AND fulfillment_id =
              :fulfillment_id
        ORDER BY id
        LIMIT 1
        """,
        {
            "company_id": (
                COMPANY_ID
            ),
            "fulfillment_id": (
                fulfillment_id
            ),
        },
    )


async def allocation_history(
    db,
    *,
    correction_event_id,
):
    return tuple(
        (
            await db.execute(
                text(
                    """
                    SELECT
                        id,
                        trade_value_correction_event_id,
                        invoice_fulfillment_allocation_id,
                        recognition_date,
                        original_allocated_base_amount,
                        corrected_allocated_base_amount,
                        currency_code,
                        reversal_of_id
                    FROM purchase_value_correction_allocation_events
                    WHERE company_id =
                          :company_id
                      AND trade_value_correction_event_id =
                          :correction_event_id
                    ORDER BY id
                    """
                ),
                {
                    "company_id":
                        COMPANY_ID,
                    "correction_event_id":
                        correction_event_id,
                },
            )
        )
        .mappings()
        .all()
    )


def active_allocation_rows(
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


async def insert_value_correction(
    db,
    *,
    invoice_id,
    invoice_line_id,
    product_id,
    correction_date,
    original_gross_amount,
    corrected_gross_amount,
    reversal_of_id=None,
    reason_code,
):
    event = (
        TradeValueCorrectionEvent(
            company_id=COMPANY_ID,
            direction="purchase",
            trade_document_id=(
                invoice_id
            ),
            trade_document_line_id=(
                invoice_line_id
            ),
            product_id=product_id,
            correction_date=(
                correction_date
            ),
            original_gross_amount=Decimal(
                original_gross_amount
            ),
            original_tax_amount=Decimal(
                "0.00"
            ),
            corrected_gross_amount=Decimal(
                corrected_gross_amount
            ),
            corrected_tax_amount=Decimal(
                "0.00"
            ),
            currency_code="UAH",
            reason_code=reason_code,
            created_by=USER_ID,
            reversal_of_id=(
                reversal_of_id
            ),
        )
    )

    db.add(
        event
    )

    await db.flush()

    assert event.id is not None

    return event


async def open_period_end(
    db,
    *,
    business_date,
):
    return await scalar_or_none(
        db,
        """
        SELECT end_date
        FROM accounting_periods
        WHERE company_id =
              :company_id
          AND status =
              'open'
          AND is_locked IS FALSE
          AND start_date <=
              :business_date
          AND end_date >=
              :business_date
        ORDER BY
            start_date DESC,
            id DESC
        LIMIT 1
        """,
        {
            "company_id":
                COMPANY_ID,
            "business_date":
                business_date,
        },
    )


@pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run the real PostgreSQL chronology test"
    ),
)
@pytest.mark.asyncio
async def test_purchase_value_correction_allocation_postgresql_chronology():
    baseline = (
        await table_counts()
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
                await create_business_fixture(
                    db
                )
            )

            d1 = fixture[
                "business_date"
            ]

            period_end = (
                await open_period_end(
                    db,
                    business_date=d1,
                )
            )

            assert period_end is not None, (
                "Real PostgreSQL E2E requires "
                "the fixture business date inside "
                "an open unlocked accounting period"
            )

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

            d5 = (
                d1
                + timedelta(
                    days=4
                )
            )

            if d5 > period_end:
                pytest.skip(
                    "Real PostgreSQL E2E requires "
                    "at least five usable business dates "
                    "inside the current open period"
                )

            token = fixture[
                "suffix"
            ]

            receipt_1 = (
                await execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=(
                        fixture[
                            "order_id"
                        ]
                    ),
                    warehouse_document_number=(
                        "PVC-PG-R1-"
                        + token
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
                                "60.0000"
                            ),
                        ),
                    ),
                )
            )

            fulfillment_line_1 = (
                await fulfillment_line_id(
                    db,
                    fulfillment_id=(
                        receipt_1
                        .fulfillment
                        .id
                    ),
                )
            )

            allocation_1 = (
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
                        receipt_1
                        .fulfillment
                        .id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_1
                    ),
                    quantity=Decimal(
                        "60.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            correction_a = (
                await insert_value_correction(
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
                        "119.98"
                    ),
                    reason_code=(
                        "postgres_e2e_after_receipt"
                    ),
                )
            )

            result_a1 = (
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
                result_a1.source_is_active
                is True
            )

            assert len(
                result_a1.created_events
            ) == 1

            a1 = (
                result_a1
                .created_events[
                    0
                ]
            )

            assert (
                a1.invoice_fulfillment_allocation_id
                == allocation_1.id
            )

            assert (
                a1.recognition_date
                == d2
            )

            assert (
                Decimal(
                    a1.original_allocated_base_amount
                )
                == Decimal(
                    "60.00"
                )
            )

            assert (
                Decimal(
                    a1.corrected_allocated_base_amount
                )
                == Decimal(
                    "59.99"
                )
            )

            repeat_a1 = (
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
                repeat_a1.created_events
                == ()
            )

            print(
                "REAL POSTGRESQL CORRECTION AFTER RECEIPT: "
                "ORIGINAL / DATE / IDEMPOTENCY = PASS"
            )

            receipt_2 = (
                await execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=(
                        fixture[
                            "order_id"
                        ]
                    ),
                    warehouse_document_number=(
                        "PVC-PG-R2-"
                        + token
                    ),
                    document_date=d3,
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
                                "60.0000"
                            ),
                        ),
                    ),
                )
            )

            fulfillment_line_2 = (
                await fulfillment_line_id(
                    db,
                    fulfillment_id=(
                        receipt_2
                        .fulfillment
                        .id
                    ),
                )
            )

            allocation_2 = (
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
                        receipt_2
                        .fulfillment
                        .id
                    ),
                    fulfillment_line_id=(
                        fulfillment_line_2
                    ),
                    quantity=Decimal(
                        "60.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            result_a2 = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction_a.id
                    ),
                    adjustment_date=d3,
                    created_by=USER_ID,
                )
            )

            assert len(
                result_a2.created_events
            ) == 1

            a2 = (
                result_a2
                .created_events[
                    0
                ]
            )

            assert (
                a2.invoice_fulfillment_allocation_id
                == allocation_2.id
            )

            assert (
                a2.recognition_date
                == d3
            )

            assert (
                Decimal(
                    a2.original_allocated_base_amount
                )
                == Decimal(
                    "60.00"
                )
            )

            assert (
                Decimal(
                    a2.corrected_allocated_base_amount
                )
                == Decimal(
                    "59.99"
                )
            )

            rows_a = (
                await allocation_history(
                    db,
                    correction_event_id=(
                        correction_a.id
                    ),
                )
            )

            assert len(
                rows_a
            ) == 2

            assert len(
                active_allocation_rows(
                    rows_a
                )
            ) == 2

            repeat_a2 = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction_a.id
                    ),
                    adjustment_date=d3,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_a2.created_events
                == ()
            )

            print(
                "REAL POSTGRESQL CORRECTION BEFORE FUTURE RECEIPT: "
                "NEW SOURCE DATE / IDEMPOTENCY = PASS"
            )

            correction_b = (
                await insert_value_correction(
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
                    correction_date=d3,
                    original_gross_amount=(
                        "120.00"
                    ),
                    corrected_gross_amount=(
                        "119.97"
                    ),
                    reason_code=(
                        "postgres_e2e_peer_rounding"
                    ),
                )
            )

            result_b1 = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction_b.id
                    ),
                    adjustment_date=d3,
                    created_by=USER_ID,
                )
            )

            assert len(
                result_b1.created_events
            ) == 2

            b_first = (
                result_b1
                .created_events[
                    0
                ]
            )

            b_second = (
                result_b1
                .created_events[
                    1
                ]
            )

            assert (
                b_first
                .invoice_fulfillment_allocation_id
                == allocation_1.id
            )

            assert (
                b_second
                .invoice_fulfillment_allocation_id
                == allocation_2.id
            )

            assert (
                Decimal(
                    b_first.corrected_allocated_base_amount
                )
                == Decimal(
                    "59.99"
                )
            )

            assert (
                Decimal(
                    b_second.corrected_allocated_base_amount
                )
                == Decimal(
                    "59.98"
                )
            )

            assert (
                b_first.recognition_date
                == d3
            )

            assert (
                b_second.recognition_date
                == d3
            )

            print(
                "REAL POSTGRESQL PEER CUMULATIVE ROUNDING: "
                "59.99 + 59.98 = 119.97 = PASS"
            )

            reversed_allocation_1 = (
                await reverse_invoice_fulfillment_allocation(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=(
                        fixture[
                            "invoice_id"
                        ]
                    ),
                    allocation_id=(
                        allocation_1.id
                    ),
                    reversed_by=USER_ID,
                )
            )

            assert str(
                reversed_allocation_1.status
            ) in {
                "reversed",
                "InvoiceFulfillmentAllocationStatus.REVERSED",
            }

            result_a3 = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction_a.id
                    ),
                    adjustment_date=d4,
                    created_by=USER_ID,
                )
            )

            assert len(
                result_a3.created_events
            ) == 1

            assert (
                result_a3.created_events[
                    0
                ].reversal_of_id
                == a1.id
            )

            assert (
                result_a3.created_events[
                    0
                ].recognition_date
                == d4
            )

            rows_a_after = (
                await allocation_history(
                    db,
                    correction_event_id=(
                        correction_a.id
                    ),
                )
            )

            active_a_after = (
                active_allocation_rows(
                    rows_a_after
                )
            )

            assert len(
                active_a_after
            ) == 1

            assert (
                active_a_after[
                    0
                ][
                    "invoice_fulfillment_allocation_id"
                ]
                == allocation_2.id
            )

            assert (
                active_a_after[
                    0
                ][
                    "recognition_date"
                ]
                == d3
            )

            print(
                "REAL POSTGRESQL REVERSED IFA / UNCHANGED PEER: "
                "REMOVAL REVERSAL + NO DATE CHURN = PASS"
            )

            result_b2 = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction_b.id
                    ),
                    adjustment_date=d4,
                    created_by=USER_ID,
                )
            )

            assert len(
                result_b2.created_events
            ) == 3

            removed_reversal = (
                result_b2
                .created_events[
                    0
                ]
            )

            peer_reversal = (
                result_b2
                .created_events[
                    1
                ]
            )

            peer_replacement = (
                result_b2
                .created_events[
                    2
                ]
            )

            assert (
                removed_reversal.reversal_of_id
                == b_first.id
            )

            assert (
                peer_reversal.reversal_of_id
                == b_second.id
            )

            assert (
                peer_replacement.reversal_of_id
                is None
            )

            assert (
                peer_replacement
                .invoice_fulfillment_allocation_id
                == allocation_2.id
            )

            assert (
                peer_replacement.recognition_date
                == d4
            )

            assert (
                Decimal(
                    peer_replacement
                    .original_allocated_base_amount
                )
                == Decimal(
                    "60.00"
                )
            )

            assert (
                Decimal(
                    peer_replacement
                    .corrected_allocated_base_amount
                )
                == Decimal(
                    "59.99"
                )
            )

            rows_b_after = (
                await allocation_history(
                    db,
                    correction_event_id=(
                        correction_b.id
                    ),
                )
            )

            active_b_after = (
                active_allocation_rows(
                    rows_b_after
                )
            )

            assert len(
                active_b_after
            ) == 1

            assert (
                active_b_after[
                    0
                ][
                    "id"
                ]
                == peer_replacement.id
            )

            repeat_b2 = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction_b.id
                    ),
                    adjustment_date=d4,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_b2.created_events
                == ()
            )

            print(
                "REAL POSTGRESQL PEER ROUNDING REALLOCATION: "
                "59.98 -> 59.99 FORWARD REPLACEMENT = PASS"
            )

            correction_b_reversal = (
                await insert_value_correction(
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
                    correction_date=d5,
                    original_gross_amount=(
                        "120.00"
                    ),
                    corrected_gross_amount=(
                        "119.97"
                    ),
                    reversal_of_id=(
                        correction_b.id
                    ),
                    reason_code=(
                        "postgres_e2e_peer_rounding_reversal"
                    ),
                )
            )

            result_b3 = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction_b_reversal.id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert (
                result_b3.source_is_active
                is False
            )

            assert (
                result_b3.desired_targets
                == ()
            )

            assert len(
                result_b3.created_events
            ) == 1

            final_reversal = (
                result_b3
                .created_events[
                    0
                ]
            )

            assert (
                final_reversal.reversal_of_id
                == peer_replacement.id
            )

            assert (
                final_reversal.recognition_date
                == d5
            )

            rows_b_final = (
                await allocation_history(
                    db,
                    correction_event_id=(
                        correction_b.id
                    ),
                )
            )

            assert (
                active_allocation_rows(
                    rows_b_final
                )
                == ()
            )

            repeat_b3 = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction_b_reversal.id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_b3.created_events
                == ()
            )

            print(
                "REAL POSTGRESQL VALUE CORRECTION REVERSAL: "
                "ALL ACTIVE ALLOCATIONS ZEROED / IDEMPOTENT = PASS"
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
        await table_counts()
    )

    assert after == baseline, (
        "\nPostgreSQL E2E rollback did not restore "
        "business data.\n"
        f"before={baseline}\n"
        f"after={after}"
    )

    print(
        "POSTGRESQL BUSINESS DATA ROLLBACK = PASS"
    )

    if scenario_error is not None:
        raise scenario_error.with_traceback(
            scenario_traceback
        )
