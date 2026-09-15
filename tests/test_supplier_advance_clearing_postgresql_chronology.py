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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import engine

from app.models.trade_value_correction_event import (
    TradeValueCorrectionEvent,
)
from app.services.purchase_value_correction_allocation_reconciliation_service import (
    reconcile_purchase_value_correction_allocations_for_event,
)
from app.services.supplier_advance_clearing_lifecycle_service import (
    reconcile_supplier_advance_clearing_lifecycle_for_invoice,
)

from app.services.invoice_fulfillment_allocation_service import (
    create_invoice_fulfillment_allocation,
    reverse_invoice_fulfillment_allocation,
)

from app.services.payment_lifecycle_service import (
    PaymentStatusError,
    cancel_payment,
    confirm_payment,
    create_payment_draft,
)

from app.services.payment_settlement_service import (
    create_payment_settlement_allocation,
    reverse_payment_settlement_allocation,
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

GL_CODES = (
    "281",
    "311",
    "371",
    "631",
)

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
    "trade_value_correction_events",
    "purchase_value_correction_allocation_events",
    "supplier_advance_clearing_events",
    "journal_entries",
    "journal_entry_lines",
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


async def gl_snapshot(
    db,
):
    values = {}

    for code in GL_CODES:
        row = (
            await db.execute(
                text(
                    """
                    SELECT
                        COALESCE(
                            SUM(
                                jel.debit
                                - jel.credit
                            ),
                            0
                        ) AS net_debit
                    FROM journal_entry_lines jel
                    JOIN journal_entries je
                      ON je.id =
                         jel.journal_entry_id
                    JOIN accounts a
                      ON a.id =
                         jel.account_id
                    WHERE je.company_id =
                          :company_id
                      AND a.company_id =
                          :company_id
                      AND a.code = :code
                    """
                ),
                {
                    "company_id": (
                        COMPANY_ID
                    ),
                    "code": code,
                },
            )
        ).scalar_one()

        values[
            code
        ] = Decimal(
            row
        )

    return values


def assert_gl_delta(
    *,
    baseline,
    actual,
    expected,
):
    delta = {
        code: (
            actual[
                code
            ]
            - baseline[
                code
            ]
        )
        for code
        in GL_CODES
    }

    assert delta == expected


async def source_journal_id(
    db,
    *,
    source_column,
    source_id,
):
    allowed = {
        "payment_id",
        (
            "supplier_advance_"
            "clearing_event_id"
        ),
    }

    if source_column not in allowed:
        raise AssertionError(
            "Unsupported JournalEntry "
            "source column"
        )

    rows = (
        await db.execute(
            text(
                f"""
                SELECT id
                FROM journal_entries
                WHERE company_id =
                      :company_id
                  AND {source_column} =
                      :source_id
                ORDER BY id
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
                "source_id": source_id,
            },
        )
    ).scalars().all()

    assert len(
        rows
    ) == 1

    return rows[
        0
    ]


async def assert_journal_posting(
    db,
    *,
    journal_entry_id,
    expected,
    expected_reversal_of_id=None,
):
    rows = (
        await db.execute(
            text(
                """
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
                FROM journal_entry_lines jel
                JOIN accounts a
                  ON a.id =
                     jel.account_id
                WHERE jel.journal_entry_id =
                      :journal_entry_id
                GROUP BY a.code
                ORDER BY a.code
                """
            ),
            {
                "journal_entry_id": (
                    journal_entry_id
                ),
            },
        )
    ).mappings().all()

    actual = {
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

    assert actual == expected

    reversal_of_id = (
        await db.execute(
            text(
                """
                SELECT reversal_of_id
                FROM journal_entries
                WHERE id = :journal_entry_id
                """
            ),
            {
                "journal_entry_id": (
                    journal_entry_id
                ),
            },
        )
    ).scalar_one()

    assert (
        reversal_of_id
        == expected_reversal_of_id
    )


async def supplier_events(
    db,
    *,
    settlement_id,
):
    return (
        await db.execute(
            text(
                """
                SELECT
                    id,
                    payment_settlement_allocation_id,
                    invoice_fulfillment_allocation_id,
                    clearing_date,
                    cleared_amount,
                    currency_code,
                    reversal_of_id
                FROM supplier_advance_clearing_events
                WHERE company_id =
                      :company_id
                  AND payment_settlement_allocation_id =
                      :settlement_id
                ORDER BY id
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
                "settlement_id": (
                    settlement_id
                ),
            },
        )
    ).mappings().all()


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

    payment = await create_payment_draft(
        db,
        company_id=COMPANY_ID,
        counterparty_id=supplier_id,
        contract_id=None,
        number=(
            "PAY-E2E-"
            + suffix
        ),
        direction="outgoing",
        payment_date=business_date,
        currency_code="UAH",
        amount=Decimal(
            "120.00"
        ),
        created_by=USER_ID,
        external_reference=None,
        description=None,
    )
    payment_id = payment.id

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


@pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run the real PostgreSQL chronology test"
    ),
)
@pytest.mark.asyncio
async def test_supplier_advance_payment_first_chronology_postgresql():
    baseline_counts = (
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

            gl_baseline = (
                await gl_snapshot(
                    db
                )
            )

            # ==================================================
            # A. PAYMENT FIRST
            #
            # Payment:
            #
            #     Dr 371 120
            #     Cr 311 120
            # ==================================================

            payment = await confirm_payment(
                db,
                company_id=COMPANY_ID,
                payment_id=(
                    fixture[
                        "payment_id"
                    ]
                ),
                confirmed_by=USER_ID,
            )

            assert (
                str(
                    payment.status
                )
                in {
                    "confirmed",
                    "PaymentStatus.CONFIRMED",
                }
            )

            payment_journal_id = (
                await source_journal_id(
                    db,
                    source_column=(
                        "payment_id"
                    ),
                    source_id=(
                        payment.id
                    ),
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    payment_journal_id
                ),
                expected={
                    "311": (
                        MONEY_ZERO,
                        Decimal(
                            "120.00"
                        ),
                    ),
                    "371": (
                        Decimal(
                            "120.00"
                        ),
                        MONEY_ZERO,
                    ),
                },
            )

            after_payment = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                baseline=gl_baseline,
                actual=after_payment,
                expected={
                    "281": Decimal("0"),
                    "311": Decimal("-120"),
                    "371": Decimal("120"),
                    "631": Decimal("0"),
                },
            )

            print(
                "PAYMENT FIRST: "
                "Dr371 / Cr311 = 120 = PASS"
            )

            # ==================================================
            # B. COMMERCIAL SETTLEMENT BEFORE RECEIPT
            #
            # Allocation is allowed commercially.
            #
            # But:
            #
            #     NO Dr631 / Cr371 yet.
            # ==================================================

            settlement = (
                await create_payment_settlement_allocation(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=(
                        payment.id
                    ),
                    open_item_id=(
                        fixture[
                            "open_item_id"
                        ]
                    ),
                    amount=Decimal(
                        "120.00"
                    ),
                    created_by=USER_ID,
                )
            )

            events = await supplier_events(
                db,
                settlement_id=(
                    settlement.id
                ),
            )

            assert events == []

            legacy_settlement_journals = (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM journal_entries
                    WHERE company_id =
                          :company_id
                      AND payment_settlement_allocation_id =
                          :settlement_id
                    """,
                    {
                        "company_id": (
                            COMPANY_ID
                        ),
                        "settlement_id": (
                            settlement.id
                        ),
                    },
                )
            )

            assert (
                legacy_settlement_journals
                == 0
            )

            after_settlement = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                baseline=gl_baseline,
                actual=after_settlement,
                expected={
                    "281": Decimal("0"),
                    "311": Decimal("-120"),
                    "371": Decimal("120"),
                    "631": Decimal("0"),
                },
            )

            print(
                "PAYABLE SETTLEMENT BEFORE RECEIPT: "
                "clearing = 0 = PASS"
            )

            # ==================================================
            # C. FIRST RECEIPT = 60
            #
            # Real purchase fulfillment posts:
            #
            #     Dr 281 60
            #     Cr 631 60
            #
            # No VAT fields are configured in this E2E,
            # so VAT lifecycle is deliberately a no-op.
            # ==================================================

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
                        "REC-E2E-1-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    document_date=(
                        fixture[
                            "business_date"
                        ]
                    ),
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

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    receipt_1
                    .journal_entry
                    .id
                ),
                expected={
                    "281": (
                        Decimal(
                            "60.00"
                        ),
                        MONEY_ZERO,
                    ),
                    "631": (
                        MONEY_ZERO,
                        Decimal(
                            "60.00"
                        ),
                    ),
                },
            )

            after_receipt_1 = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                baseline=gl_baseline,
                actual=after_receipt_1,
                expected={
                    "281": Decimal("60"),
                    "311": Decimal("-120"),
                    "371": Decimal("120"),
                    "631": Decimal("-60"),
                },
            )

            print(
                "FIRST RECEIPT: "
                "Dr281 / Cr631 = 60 = PASS"
            )

            first_fulfillment_line_id = (
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
                        first_fulfillment_line_id
                    ),
                    quantity=Decimal(
                        "60.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            tax_calculation_count = (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM tax_calculations
                    WHERE company_id =
                          :company_id
                      AND trade_document_id =
                          :invoice_id
                    """,
                    {
                        "company_id": (
                            COMPANY_ID
                        ),
                        "invoice_id": (
                            fixture[
                                "invoice_id"
                            ]
                        ),
                    },
                )
            )

            assert (
                tax_calculation_count
                == 0
            )

            input_bridge_count = (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM input_vat_fulfillment_bridge_events
                    WHERE company_id =
                          :company_id
                      AND invoice_fulfillment_allocation_id =
                          :allocation_id
                    """,
                    {
                        "company_id": (
                            COMPANY_ID
                        ),
                        "allocation_id": (
                            allocation_1.id
                        ),
                    },
                )
            )

            assert (
                input_bridge_count
                == 0
            )

            events = await supplier_events(
                db,
                settlement_id=(
                    settlement.id
                ),
            )

            assert len(
                events
            ) == 1

            first_event = events[
                0
            ]

            assert (
                first_event[
                    "reversal_of_id"
                ]
                is None
            )

            assert (
                first_event[
                    "invoice_fulfillment_allocation_id"
                ]
                == allocation_1.id
            )

            assert Decimal(
                first_event[
                    "cleared_amount"
                ]
            ) == Decimal(
                "60.00"
            )

            assert (
                first_event[
                    "currency_code"
                ]
                == "UAH"
            )

            first_clearing_journal = (
                await source_journal_id(
                    db,
                    source_column=(
                        "supplier_advance_"
                        "clearing_event_id"
                    ),
                    source_id=(
                        first_event[
                            "id"
                        ]
                    ),
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    first_clearing_journal
                ),
                expected={
                    "371": (
                        MONEY_ZERO,
                        Decimal(
                            "60.00"
                        ),
                    ),
                    "631": (
                        Decimal(
                            "60.00"
                        ),
                        MONEY_ZERO,
                    ),
                },
            )

            after_clearing_1 = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                baseline=gl_baseline,
                actual=after_clearing_1,
                expected={
                    "281": Decimal("60"),
                    "311": Decimal("-120"),
                    "371": Decimal("60"),
                    "631": Decimal("0"),
                },
            )

            print(
                "FIRST CLEARING: "
                "Dr631 / Cr371 = 60 = PASS"
            )

            # ==================================================
            # D. SECOND RECEIPT = 60
            # ==================================================

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
                        "REC-E2E-2-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    document_date=(
                        fixture[
                            "business_date"
                        ]
                    ),
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

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    receipt_2
                    .journal_entry
                    .id
                ),
                expected={
                    "281": (
                        Decimal(
                            "60.00"
                        ),
                        MONEY_ZERO,
                    ),
                    "631": (
                        MONEY_ZERO,
                        Decimal(
                            "60.00"
                        ),
                    ),
                },
            )

            after_receipt_2 = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                baseline=gl_baseline,
                actual=after_receipt_2,
                expected={
                    "281": Decimal("120"),
                    "311": Decimal("-120"),
                    "371": Decimal("60"),
                    "631": Decimal("-60"),
                },
            )

            print(
                "SECOND RECEIPT: "
                "Dr281 / Cr631 = 60 = PASS"
            )

            second_fulfillment_line_id = (
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
                        second_fulfillment_line_id
                    ),
                    quantity=Decimal(
                        "60.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            events = await supplier_events(
                db,
                settlement_id=(
                    settlement.id
                ),
            )

            assert len(
                events
            ) == 2

            original_events = [
                row
                for row in events
                if (
                    row[
                        "reversal_of_id"
                    ]
                    is None
                )
            ]

            assert len(
                original_events
            ) == 2

            assert sum(
                Decimal(
                    row[
                        "cleared_amount"
                    ]
                )
                for row
                in original_events
            ) == Decimal(
                "120.00"
            )

            second_event = next(
                row
                for row
                in original_events
                if (
                    row[
                        "invoice_fulfillment_allocation_id"
                    ]
                    == allocation_2.id
                )
            )

            second_clearing_journal = (
                await source_journal_id(
                    db,
                    source_column=(
                        "supplier_advance_"
                        "clearing_event_id"
                    ),
                    source_id=(
                        second_event[
                            "id"
                        ]
                    ),
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    second_clearing_journal
                ),
                expected={
                    "371": (
                        MONEY_ZERO,
                        Decimal(
                            "60.00"
                        ),
                    ),
                    "631": (
                        Decimal(
                            "60.00"
                        ),
                        MONEY_ZERO,
                    ),
                },
            )

            after_clearing_2 = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                baseline=gl_baseline,
                actual=after_clearing_2,
                expected={
                    "281": Decimal("120"),
                    "311": Decimal("-120"),
                    "371": Decimal("0"),
                    "631": Decimal("0"),
                },
            )

            print(
                "SECOND CLEARING: "
                "Dr631 / Cr371 = 60 = PASS"
            )

            print(
                "PAYMENT-FIRST FINAL BEFORE REVERSAL: "
                "371 = 0 AND 631 = 0 = PASS"
            )

            # ==================================================
            # E. ECONOMIC MATCH REVERSAL
            #
            # Reverse second InvoiceFulfillmentAllocation.
            #
            # Supplier clearing reversal:
            #
            #     Dr 371 60
            #     Cr 631 60
            # ==================================================

            await reverse_invoice_fulfillment_allocation(
                db,
                company_id=COMPANY_ID,
                invoice_id=(
                    fixture[
                        "invoice_id"
                    ]
                ),
                allocation_id=(
                    allocation_2.id
                ),
                reversed_by=USER_ID,
            )

            events = await supplier_events(
                db,
                settlement_id=(
                    settlement.id
                ),
            )

            assert len(
                events
            ) == 3

            second_reversal = next(
                row
                for row in events
                if (
                    row[
                        "reversal_of_id"
                    ]
                    == second_event[
                        "id"
                    ]
                )
            )

            assert Decimal(
                second_reversal[
                    "cleared_amount"
                ]
            ) == Decimal(
                "60.00"
            )

            second_reversal_journal = (
                await source_journal_id(
                    db,
                    source_column=(
                        "supplier_advance_"
                        "clearing_event_id"
                    ),
                    source_id=(
                        second_reversal[
                            "id"
                        ]
                    ),
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    second_reversal_journal
                ),
                expected={
                    "371": (
                        Decimal(
                            "60.00"
                        ),
                        MONEY_ZERO,
                    ),
                    "631": (
                        MONEY_ZERO,
                        Decimal(
                            "60.00"
                        ),
                    ),
                },
                expected_reversal_of_id=(
                    second_clearing_journal
                ),
            )

            after_allocation_reversal = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                baseline=gl_baseline,
                actual=(
                    after_allocation_reversal
                ),
                expected={
                    "281": Decimal("120"),
                    "311": Decimal("-120"),
                    "371": Decimal("60"),
                    "631": Decimal("-60"),
                },
            )

            allocation_2_status = (
                await scalar(
                    db,
                    """
                    SELECT status
                    FROM invoice_fulfillment_allocations
                    WHERE id =
                          :allocation_id
                    """,
                    {
                        "allocation_id": (
                            allocation_2.id
                        ),
                    },
                )
            )

            assert (
                allocation_2_status
                == "reversed"
            )

            print(
                "FULFILLMENT ALLOCATION REVERSAL: "
                "Dr371 / Cr631 = 60 = PASS"
            )

            # ==================================================
            # E2. ACTIVE SETTLEMENT BLOCKS PAYMENT CANCELLATION
            # ==================================================

            with pytest.raises(
                PaymentStatusError,
                match=(
                    "Payment has ACTIVE settlement "
                    "allocations; reverse them before "
                    "cancellation"
                ),
            ):
                await cancel_payment(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=fixture[
                        "payment_id"
                    ],
                    cancelled_by=USER_ID,
                )

            payment_status_after_blocked_cancel = (
                await scalar(
                    db,
                    """
                    SELECT status
                    FROM payments
                    WHERE id = :payment_id
                    """,
                    {
                        "payment_id": fixture[
                            "payment_id"
                        ],
                    },
                )
            )

            assert (
                payment_status_after_blocked_cancel
                == "confirmed"
            )

            print(
                "ACTIVE SETTLEMENT CANCEL GUARD = PASS"
            )

            # ==================================================
            # F. COMMERCIAL SETTLEMENT REVERSAL
            #
            # Remaining first clearing is reversed:
            #
            #     Dr 371 60
            #     Cr 631 60
            #
            # Result:
            #
            # - payment still exists as supplier advance 371
            # - both receipts remain economic supplier
            #   liabilities on 631
            # - commercial settlement = 0
            # ==================================================

            await reverse_payment_settlement_allocation(
                db,
                company_id=COMPANY_ID,
                allocation_id=(
                    settlement.id
                ),
                reversed_by=USER_ID,
            )

            events = await supplier_events(
                db,
                settlement_id=(
                    settlement.id
                ),
            )

            assert len(
                events
            ) == 4

            first_reversal = next(
                row
                for row in events
                if (
                    row[
                        "reversal_of_id"
                    ]
                    == first_event[
                        "id"
                    ]
                )
            )

            first_reversal_journal = (
                await source_journal_id(
                    db,
                    source_column=(
                        "supplier_advance_"
                        "clearing_event_id"
                    ),
                    source_id=(
                        first_reversal[
                            "id"
                        ]
                    ),
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    first_reversal_journal
                ),
                expected={
                    "371": (
                        Decimal(
                            "60.00"
                        ),
                        MONEY_ZERO,
                    ),
                    "631": (
                        MONEY_ZERO,
                        Decimal(
                            "60.00"
                        ),
                    ),
                },
                expected_reversal_of_id=(
                    first_clearing_journal
                ),
            )

            final_gl = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                baseline=gl_baseline,
                actual=final_gl,
                expected={
                    "281": Decimal("120"),
                    "311": Decimal("-120"),
                    "371": Decimal("120"),
                    "631": Decimal("-120"),
                },
            )

            settlement_status = (
                await scalar(
                    db,
                    """
                    SELECT status
                    FROM payment_settlement_allocations
                    WHERE id =
                          :settlement_id
                    """,
                    {
                        "settlement_id": (
                            settlement.id
                        ),
                    },
                )
            )

            assert (
                settlement_status
                == "reversed"
            )

            open_item_status = (
                await scalar(
                    db,
                    """
                    SELECT status
                    FROM counterparty_open_items
                    WHERE id =
                          :open_item_id
                    """,
                    {
                        "open_item_id": (
                            fixture[
                                "open_item_id"
                            ]
                        ),
                    },
                )
            )

            assert (
                open_item_status
                == "open"
            )

            originals = [
                row
                for row in events
                if (
                    row[
                        "reversal_of_id"
                    ]
                    is None
                )
            ]

            reversals = [
                row
                for row in events
                if (
                    row[
                        "reversal_of_id"
                    ]
                    is not None
                )
            ]

            assert len(
                originals
            ) == 2

            assert len(
                reversals
            ) == 2

            assert {
                row[
                    "reversal_of_id"
                ]
                for row
                in reversals
            } == {
                row[
                    "id"
                ]
                for row
                in originals
            }

            typed_journal_count = (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM journal_entries
                    WHERE company_id =
                          :company_id
                      AND supplier_advance_clearing_event_id
                          IN (
                              SELECT id
                              FROM supplier_advance_clearing_events
                              WHERE company_id =
                                    :company_id
                                AND payment_settlement_allocation_id =
                                    :settlement_id
                          )
                    """,
                    {
                        "company_id": (
                            COMPANY_ID
                        ),
                        "settlement_id": (
                            settlement.id
                        ),
                    },
                )
            )

            assert (
                typed_journal_count
                == 4
            )

            print(
                "SETTLEMENT REVERSAL: "
                "remaining Dr371 / Cr631 = 60 = PASS"
            )

            print(
                "IMMUTABLE EVENT HISTORY: "
                "2 ORIGINAL + 2 REVERSAL = PASS"
            )

            print(
                "SUPPLIER-TYPED JOURNALS = 4 = PASS"
            )

            # ==================================================
            # G. CANCEL CONFIRMED PAYMENT AFTER SETTLEMENT
            #    REVERSAL
            #
            # Original outgoing payment:
            #   Dr 371 120 / Cr 311 120
            #
            # Cancellation reversal:
            #   Dr 311 120 / Cr 371 120
            #
            # Final net:
            #   311 = 0
            #   371 = 0
            #   281 = Dr120
            #   631 = Cr120
            # ==================================================

            payment_journal_id = (
                await source_journal_id(
                    db,
                    source_column="payment_id",
                    source_id=fixture[
                        "payment_id"
                    ],
                )
            )

            cancelled_payment = (
                await cancel_payment(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=fixture[
                        "payment_id"
                    ],
                    cancelled_by=USER_ID,
                )
            )

            assert (
                cancelled_payment.status.value
                == "cancelled"
            )
            assert (
                cancelled_payment.cancelled_by
                == USER_ID
            )
            assert (
                cancelled_payment.cancelled_at
                is not None
            )

            reversal_rows = (
                await db.execute(
                    text(
                        """
                        SELECT id
                        FROM journal_entries
                        WHERE company_id = :company_id
                          AND reversal_of_id = :original_id
                        ORDER BY id
                        """
                    ),
                    {
                        "company_id": COMPANY_ID,
                        "original_id": (
                            payment_journal_id
                        ),
                    },
                )
            ).scalars().all()

            assert len(
                reversal_rows
            ) == 1

            payment_reversal_journal_id = (
                reversal_rows[0]
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    payment_reversal_journal_id
                ),
                expected={
                    "311": (
                        Decimal("120.00"),
                        MONEY_ZERO,
                    ),
                    "371": (
                        MONEY_ZERO,
                        Decimal("120.00"),
                    ),
                },
                expected_reversal_of_id=(
                    payment_journal_id
                ),
            )

            after_payment_cancel = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                baseline=gl_baseline,
                actual=after_payment_cancel,
                expected={
                    "281": Decimal("120"),
                    "311": MONEY_ZERO,
                    "371": MONEY_ZERO,
                    "631": Decimal("-120"),
                },
            )

            final_settlement_status = (
                await scalar(
                    db,
                    """
                    SELECT status
                    FROM payment_settlement_allocations
                    WHERE id = :settlement_id
                    """,
                    {
                        "settlement_id": (
                            settlement.id
                        ),
                    },
                )
            )

            assert (
                final_settlement_status
                == "reversed"
            )

            final_open_item_status = (
                await scalar(
                    db,
                    """
                    SELECT status
                    FROM counterparty_open_items
                    WHERE id = :open_item_id
                    """,
                    {
                        "open_item_id": fixture[
                            "open_item_id"
                        ],
                    },
                )
            )

            assert (
                final_open_item_status
                == "open"
            )

            with pytest.raises(
                PaymentStatusError,
                match=(
                    "Only draft or confirmed Payments "
                    "can be cancelled"
                ),
            ):
                await cancel_payment(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=fixture[
                        "payment_id"
                    ],
                    cancelled_by=USER_ID,
                )

            reversal_count_after_reentry = (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM journal_entries
                    WHERE company_id = :company_id
                      AND reversal_of_id = :original_id
                    """,
                    {
                        "company_id": COMPANY_ID,
                        "original_id": (
                            payment_journal_id
                        ),
                    },
                )
            )

            assert (
                reversal_count_after_reentry
                == 1
            )

            print(
                "SUPPLIER PAYMENT CANCELLATION: "
                "Dr311 / Cr371 = 120 = PASS"
            )
            print(
                "PAYMENT REVERSAL PROVENANCE = PASS"
            )
            print(
                "PAYMENT CANCEL RE-ENTRY = REJECTED = PASS"
            )

        except BaseException as exc:
            scenario_error = exc
            scenario_traceback = (
                sys.exc_info()[
                    2
                ]
            )

        finally:
            await db.close()

            if transaction.is_active:
                await transaction.rollback()

    # ======================================================
    # G. PROVE ROLLBACK
    # ======================================================

    after_counts = (
        await table_counts()
    )

    assert (
        after_counts
        == baseline_counts
    ), (
        "PostgreSQL E2E rollback did not restore "
        "the exact baseline counts.\n"
        f"before={baseline_counts}\n"
        f"after={after_counts}"
    )

    print(
        "FULL E2E TRANSACTION ROLLBACK = PASS"
    )

    if scenario_error is not None:
        raise scenario_error.with_traceback(
            scenario_traceback
        )


@pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run the real PostgreSQL chronology test"
    ),
)
@pytest.mark.asyncio
async def test_purchase_value_correction_supplier_clearing_forward_only_postgresql_chronology():
    """
    Real PostgreSQL chronology:

        D1 payment:
            Dr371 / Cr311 = 120

        D1 receipt:
            Dr281 / Cr631 = 120

        D1 supplier advance clearing:
            Dr631 / Cr371 = 120

        D5 Purchase Value Correction:
            economic base 120 -> 108

        D5 supplier-clearing correction:
            reversal:
                Dr371 / Cr631 = 120

            replacement:
                Dr631 / Cr371 = 108

        second D5 reconcile:
            NOOP

    Important milestone boundary:

    Purchase Value Correction FIFO / inventory GL is NOT implemented
    by this test yet.

    Therefore after supplier-clearing correction alone:

        371 net = +12
        631 net = -12

    The following FIFO GL milestone will account for the actual
    value correction against inventory / historical cost destination
    and complete the accounting effect on 631.
    """

    # pytest-asyncio may execute sibling async tests
    # on different event loops. The shared AsyncEngine
    # pool can otherwise retain an asyncpg connection
    # bound to the previous test loop.
    #
    # Replace the old pool without attempting to close
    # old-loop connections from the new loop.
    await engine.dispose(close=False)

    baseline_counts = (
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

            d5 = (
                d1
                + timedelta(
                    days=4
                )
            )

            period_end = await scalar_or_none(
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
                        d1,
                },
            )

            assert period_end is not None, (
                "Real PostgreSQL PVC -> 631/371 E2E "
                "requires D1 inside an open unlocked "
                "accounting period"
            )

            if d5 > period_end:
                pytest.skip(
                    "Real PostgreSQL PVC -> 631/371 E2E "
                    "requires D1..D5 inside the same "
                    "open unlocked accounting period"
                )

            token = fixture[
                "suffix"
            ]

            gl_baseline = (
                await gl_snapshot(
                    db
                )
            )

            # ==================================================
            # A. D1 PAYMENT = 120
            #
            # Dr371 / Cr311
            # ==================================================

            payment = await confirm_payment(
                db,
                company_id=COMPANY_ID,
                payment_id=(
                    fixture[
                        "payment_id"
                    ]
                ),
                confirmed_by=USER_ID,
            )

            settlement = (
                await create_payment_settlement_allocation(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=payment.id,
                    open_item_id=(
                        fixture[
                            "open_item_id"
                        ]
                    ),
                    amount=Decimal(
                        "120.00"
                    ),
                    created_by=USER_ID,
                )
            )

            before_receipt_events = (
                await supplier_events(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            assert (
                before_receipt_events
                == []
            )

            # ==================================================
            # B. D1 RECEIPT = 120
            #
            # Dr281 / Cr631
            # ==================================================

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
                        "PVC-631-D1-"
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
                                "120.0000"
                            ),
                        ),
                    ),
                )
            )

            receipt_line_id = (
                await fulfillment_line_id(
                    db,
                    fulfillment_id=(
                        receipt
                        .fulfillment
                        .id
                    ),
                )
            )

            # ==================================================
            # C. D1 IFA = 120
            #
            # Commercial settlement already exists.
            # Creating economic fulfillment allocation makes
            # supplier clearing eligible:
            #
            # Dr631 / Cr371 = 120
            # ==================================================

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
                        receipt_line_id
                    ),
                    quantity=Decimal(
                        "120.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            initial_events = (
                await supplier_events(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            assert len(
                initial_events
            ) == 1

            original_clearing = (
                initial_events[
                    0
                ]
            )

            assert (
                original_clearing[
                    "reversal_of_id"
                ]
                is None
            )

            assert (
                original_clearing[
                    "invoice_fulfillment_allocation_id"
                ]
                == allocation.id
            )

            assert (
                original_clearing[
                    "clearing_date"
                ]
                == d1
            )

            assert Decimal(
                original_clearing[
                    "cleared_amount"
                ]
            ) == Decimal(
                "120.00"
            )

            original_clearing_journal = (
                await source_journal_id(
                    db,
                    source_column=(
                        "supplier_advance_"
                        "clearing_event_id"
                    ),
                    source_id=(
                        original_clearing[
                            "id"
                        ]
                    ),
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    original_clearing_journal
                ),
                expected={
                    "371": (
                        MONEY_ZERO,
                        Decimal(
                            "120.00"
                        ),
                    ),
                    "631": (
                        Decimal(
                            "120.00"
                        ),
                        MONEY_ZERO,
                    ),
                },
            )

            original_entry_date = (
                await scalar(
                    db,
                    """
                    SELECT entry_date
                    FROM journal_entries
                    WHERE id =
                          :journal_entry_id
                    """,
                    {
                        "journal_entry_id":
                            original_clearing_journal,
                    },
                )
            )

            assert (
                original_entry_date
                == d1
            )

            before_correction_gl = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                baseline=gl_baseline,
                actual=before_correction_gl,
                expected={
                    "281": Decimal(
                        "120"
                    ),
                    "311": Decimal(
                        "-120"
                    ),
                    "371": Decimal(
                        "0"
                    ),
                    "631": Decimal(
                        "0"
                    ),
                },
            )

            print(
                "D1 ORIGINAL CLEARING: "
                "Dr631 / Cr371 = 120 = PASS"
            )

            # ==================================================
            # D. D5 COMMERCIAL PURCHASE VALUE CORRECTION
            #
            # 120 -> 108
            #
            # This source event itself does NOT post GL and does
            # NOT directly mutate supplier clearing.
            # ==================================================

            correction = (
                TradeValueCorrectionEvent(
                    company_id=COMPANY_ID,
                    direction="purchase",
                    trade_document_id=(
                        fixture[
                            "invoice_id"
                        ]
                    ),
                    trade_document_line_id=(
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
                        "postgres_pvc_631_371_forward_only"
                    ),
                    created_by=USER_ID,
                    reversal_of_id=None,
                )
            )

            db.add(
                correction
            )

            await db.flush()

            assert (
                correction.id
                is not None
            )

            pvc_result = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=(
                        correction.id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert (
                pvc_result.source_is_active
                is True
            )

            assert len(
                pvc_result.created_events
            ) == 1

            pvc_allocation = (
                pvc_result
                .created_events[
                    0
                ]
            )

            assert (
                pvc_allocation
                .invoice_fulfillment_allocation_id
                == allocation.id
            )

            assert (
                pvc_allocation
                .recognition_date
                == d5
            )

            assert Decimal(
                pvc_allocation
                .original_allocated_base_amount
            ) == Decimal(
                "120.00"
            )

            assert Decimal(
                pvc_allocation
                .corrected_allocated_base_amount
            ) == Decimal(
                "108.00"
            )

            assert (
                pvc_allocation
                .allocated_base_delta
                == Decimal(
                    "-12.00"
                )
            )

            after_pvca_before_clearing = (
                await gl_snapshot(
                    db
                )
            )

            assert (
                after_pvca_before_clearing
                == before_correction_gl
            )

            print(
                "D5 PVC ECONOMIC ALLOCATION: "
                "120 -> 108 / DELTA -12 = PASS"
            )

            # ==================================================
            # E. D5 RECONCILE ECONOMIC 631 -> SUPPLIER CLEARING
            #
            # Economic liability capacity becomes 108.
            #
            # Existing clearing is 120.
            #
            # Immutable chronology MUST be:
            #
            # D1 original 120
            # -> D5 reversal 120
            # -> D5 replacement 108
            # ==================================================

            clearing_result = (
                await reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=(
                        fixture[
                            "invoice_id"
                        ]
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            liability_total = sum(
                (
                    Decimal(
                        candidate.amount
                    )
                    for candidate
                    in clearing_result
                    .liability_candidates
                ),
                Decimal(
                    "0.00"
                ),
            )

            assert (
                liability_total
                == Decimal(
                    "108.00"
                )
            )

            assert len(
                clearing_result
                .created_events
            ) == 2

            reversal_event = (
                clearing_result
                .created_events[
                    0
                ]
            )

            replacement_event = (
                clearing_result
                .created_events[
                    1
                ]
            )

            assert (
                reversal_event
                .reversal_of_id
                == original_clearing[
                    "id"
                ]
            )

            assert (
                reversal_event
                .clearing_date
                == d5
            )

            assert Decimal(
                reversal_event
                .cleared_amount
            ) == Decimal(
                "120.00"
            )

            assert (
                replacement_event
                .reversal_of_id
                is None
            )

            assert (
                replacement_event
                .clearing_date
                == d5
            )

            assert Decimal(
                replacement_event
                .cleared_amount
            ) == Decimal(
                "108.00"
            )

            assert (
                replacement_event
                .payment_settlement_allocation_id
                == settlement.id
            )

            assert (
                replacement_event
                .invoice_fulfillment_allocation_id
                == allocation.id
            )

            history = (
                await supplier_events(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            assert len(
                history
            ) == 3

            reversed_ids = {
                row[
                    "reversal_of_id"
                ]
                for row
                in history
                if (
                    row[
                        "reversal_of_id"
                    ]
                    is not None
                )
            }

            active_originals = [
                row
                for row
                in history
                if (
                    row[
                        "reversal_of_id"
                    ]
                    is None
                    and row[
                        "id"
                    ]
                    not in reversed_ids
                )
            ]

            assert len(
                active_originals
            ) == 1

            active_clearing = (
                active_originals[
                    0
                ]
            )

            assert (
                active_clearing[
                    "id"
                ]
                == replacement_event.id
            )

            assert (
                active_clearing[
                    "clearing_date"
                ]
                == d5
            )

            assert Decimal(
                active_clearing[
                    "cleared_amount"
                ]
            ) == Decimal(
                "108.00"
            )

            print(
                "ECONOMIC 631 CURRENT TRUTH = 108 = PASS"
            )

            print(
                "CLEARING HISTORY: "
                "D1 ORIGINAL 120 -> "
                "D5 REVERSAL 120 -> "
                "D5 REPLACEMENT 108 = PASS"
            )

            # ==================================================
            # F. JOURNAL ENTRIES MUST ALSO BE D5
            # ==================================================

            reversal_journal_id = (
                await source_journal_id(
                    db,
                    source_column=(
                        "supplier_advance_"
                        "clearing_event_id"
                    ),
                    source_id=(
                        reversal_event.id
                    ),
                )
            )

            replacement_journal_id = (
                await source_journal_id(
                    db,
                    source_column=(
                        "supplier_advance_"
                        "clearing_event_id"
                    ),
                    source_id=(
                        replacement_event.id
                    ),
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    reversal_journal_id
                ),
                expected_reversal_of_id=original_clearing_journal,
                expected={
                    "371": (
                        Decimal(
                            "120.00"
                        ),
                        MONEY_ZERO,
                    ),
                    "631": (
                        MONEY_ZERO,
                        Decimal(
                            "120.00"
                        ),
                    ),
                },
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    replacement_journal_id
                ),
                expected={
                    "371": (
                        MONEY_ZERO,
                        Decimal(
                            "108.00"
                        ),
                    ),
                    "631": (
                        Decimal(
                            "108.00"
                        ),
                        MONEY_ZERO,
                    ),
                },
            )

            reversal_journal_date = (
                await scalar(
                    db,
                    """
                    SELECT entry_date
                    FROM journal_entries
                    WHERE id =
                          :journal_entry_id
                    """,
                    {
                        "journal_entry_id":
                            reversal_journal_id,
                    },
                )
            )

            replacement_journal_date = (
                await scalar(
                    db,
                    """
                    SELECT entry_date
                    FROM journal_entries
                    WHERE id =
                          :journal_entry_id
                    """,
                    {
                        "journal_entry_id":
                            replacement_journal_id,
                    },
                )
            )

            assert (
                reversal_journal_date
                == d5
            )

            assert (
                replacement_journal_date
                == d5
            )

            print(
                "D5 REVERSAL JOURNAL DATE = PASS"
            )

            print(
                "D5 REPLACEMENT JOURNAL DATE = PASS"
            )

            # ==================================================
            # G. CURRENT GL AT THIS MILESTONE BOUNDARY
            #
            # PVC inventory / issued-cost GL is intentionally
            # still NOT posted.
            #
            # Supplier clearing alone therefore leaves:
            #
            # 371 = +12
            # 631 = -12
            #
            # The next FIFO GL milestone completes the PVC
            # accounting side.
            # ==================================================

            after_correction_gl = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                baseline=gl_baseline,
                actual=after_correction_gl,
                expected={
                    "281": Decimal(
                        "120"
                    ),
                    "311": Decimal(
                        "-120"
                    ),
                    "371": Decimal(
                        "12"
                    ),
                    "631": Decimal(
                        "-12"
                    ),
                },
            )

            print(
                "POST-CLEARING GL: "
                "371 = +12 / 631 = -12 "
                "(BEFORE PVC FIFO GL) = PASS"
            )

            # ==================================================
            # H. SECOND D5 RECONCILE MUST BE NOOP
            # ==================================================

            journal_count_before_repeat = (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM journal_entries
                    WHERE company_id =
                          :company_id
                      AND supplier_advance_clearing_event_id
                          IN (
                              SELECT id
                              FROM supplier_advance_clearing_events
                              WHERE company_id =
                                    :company_id
                                AND payment_settlement_allocation_id =
                                    :settlement_id
                          )
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "settlement_id":
                            settlement.id,
                    },
                )
            )

            assert (
                journal_count_before_repeat
                == 3
            )

            repeat_result = (
                await reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=(
                        fixture[
                            "invoice_id"
                        ]
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_result
                .created_events
                == ()
            )

            assert (
                repeat_result
                .reconciliation_targets
                == ()
            )

            repeat_history = (
                await supplier_events(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            assert len(
                repeat_history
            ) == 3

            journal_count_after_repeat = (
                await scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM journal_entries
                    WHERE company_id =
                          :company_id
                      AND supplier_advance_clearing_event_id
                          IN (
                              SELECT id
                              FROM supplier_advance_clearing_events
                              WHERE company_id =
                                    :company_id
                                AND payment_settlement_allocation_id =
                                    :settlement_id
                          )
                    """,
                    {
                        "company_id":
                            COMPANY_ID,
                        "settlement_id":
                            settlement.id,
                    },
                )
            )

            assert (
                journal_count_after_repeat
                == journal_count_before_repeat
            )

            after_repeat_gl = (
                await gl_snapshot(
                    db
                )
            )

            assert (
                after_repeat_gl
                == after_correction_gl
            )

            print(
                "SECOND D5 RECONCILE = NOOP = PASS"
            )

            print(
                "NO DUPLICATE SUPPLIER CLEARING JOURNALS = PASS"
            )

            # ==================================================
            # I. SOURCE HISTORY MUST REMAIN IMMUTABLE
            # ==================================================

            stored_original = (
                await db.execute(
                    text(
                        """
                        SELECT
                            clearing_date,
                            cleared_amount,
                            reversal_of_id
                        FROM supplier_advance_clearing_events
                        WHERE id =
                              :event_id
                        """
                    ),
                    {
                        "event_id":
                            original_clearing[
                                "id"
                            ],
                    },
                )
            ).mappings().one()

            assert (
                stored_original[
                    "clearing_date"
                ]
                == d1
            )

            assert Decimal(
                stored_original[
                    "cleared_amount"
                ]
            ) == Decimal(
                "120.00"
            )

            assert (
                stored_original[
                    "reversal_of_id"
                ]
                is None
            )

            print(
                "D1 ORIGINAL EVENT UNCHANGED = PASS"
            )

        except BaseException as exc:
            scenario_error = exc
            scenario_traceback = (
                sys.exc_info()[
                    2
                ]
            )

        finally:
            await db.close()

            if transaction.is_active:
                await transaction.rollback()

    # ======================================================
    # J. PROVE FULL DATABASE ROLLBACK
    # ======================================================

    after_counts = (
        await table_counts()
    )

    assert (
        after_counts
        == baseline_counts
    ), (
        "PVC -> 631/371 PostgreSQL E2E rollback "
        "did not restore exact baseline counts.\n"
        f"before={baseline_counts}\n"
        f"after={after_counts}"
    )

    print(
        "PVC -> 631/371 FULL TRANSACTION ROLLBACK = PASS"
    )

    if scenario_error is not None:
        raise scenario_error.with_traceback(
            scenario_traceback
        )
