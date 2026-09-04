import calendar
import os
from datetime import (
    date,
    datetime,
    timezone,
)
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

from app.core.database import engine
from app.models.contract import (
    Contract,
    ContractStatus,
    ContractType,
)
from app.models.counterparty import (
    Counterparty,
    CounterpartyType,
    CounterpartyVatStatus,
)
from app.models.counterparty_open_item import (
    CounterpartyOpenItem,
)
from app.models.customer_advance_clearing_event import (
    CustomerAdvanceClearingEvent,
)
from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.document_line import (
    DocumentLine,
)
from app.models.payment import Payment
from app.models.sales_recognition_event import (
    SalesRecognitionEvent,
)
from app.models.trade_document import (
    TradeDocument,
)
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.models.trade_fulfillment import (
    TradeFulfillment,
)
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)
from app.services.invoice_fulfillment_allocation_service import (
    create_invoice_fulfillment_allocation,
)
from app.services.payment_lifecycle_service import (
    confirm_payment,
)
from app.services.payment_settlement_service import (
    create_payment_settlement_allocation,
    get_open_item_settlement_balance,
    get_payment_settlement_reconciliation,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentStatus,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


COMPANY_ID = 1
USER_ID = 1
CURRENCY = "UAH"

ZERO_MONEY = Decimal("0.00")
ONE = Decimal("1.0000")
TWO = Decimal("2.0000")
SIXTY = Decimal("60.00")
ONE_TWENTY = Decimal("120.00")

BUSINESS_DATE = datetime.now(
    timezone.utc
).date()

RUN_POSTGRES_E2E = (
    os.getenv(
        "RUN_POSTGRES_E2E"
    )
    == "1"
)

COUNT_TABLES = (
    "accounting_periods",
    "counterparties",
    "contracts",
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
    "sales_recognition_events",
    "customer_advance_clearing_events",
    "journal_entries",
    "journal_entry_lines",
)

GL_CODES = (
    "311",
    "361",
    "681",
    "702",
)

SOURCE_COLUMNS = {
    "payment": "payment_id",
    "settlement": (
        "payment_settlement_allocation_id"
    ),
    "sales_recognition": (
        "sales_recognition_event_id"
    ),
    "customer_clearing": (
        "customer_advance_clearing_event_id"
    ),
}


def enum_value(
    value,
) -> str:
    return str(
        getattr(
            value,
            "value",
            value,
        )
    )


async def table_counts():
    result = {}

    async with engine.connect() as connection:
        for table_name in COUNT_TABLES:
            count = (
                await connection.execute(
                    text(
                        f"""
                        SELECT COUNT(*)
                        FROM {table_name}
                        """
                    )
                )
            ).scalar_one()

            result[
                table_name
            ] = int(
                count
            )

        await connection.rollback()

    return result


async def gl_snapshot(
    db: AsyncSession,
) -> dict[
    str,
    tuple[
        Decimal,
        Decimal,
    ],
]:
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    a.code,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN je.status = 'posted'
                                THEN jel.debit
                                ELSE 0
                            END
                        ),
                        0
                    ) AS debit,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN je.status = 'posted'
                                THEN jel.credit
                                ELSE 0
                            END
                        ),
                        0
                    ) AS credit
                FROM accounts a
                LEFT JOIN journal_entry_lines jel
                  ON jel.account_id = a.id
                LEFT JOIN journal_entries je
                  ON je.id =
                         jel.journal_entry_id
                 AND je.company_id =
                         a.company_id
                WHERE a.company_id =
                      :company_id
                  AND a.code IN (
                      '311',
                      '361',
                      '681',
                      '702'
                  )
                GROUP BY a.code
                ORDER BY a.code
                """
            ),
            {
                "company_id": COMPANY_ID,
            },
        )
    ).mappings().all()

    snapshot = {
        row["code"]: (
            Decimal(
                row["debit"]
            ),
            Decimal(
                row["credit"]
            ),
        )
        for row in rows
    }

    assert set(
        snapshot
    ) == set(
        GL_CODES
    )

    return snapshot


def gl_delta(
    *,
    before,
    after,
):
    return {
        code: (
            after[code][0]
            - before[code][0],
            after[code][1]
            - before[code][1],
        )
        for code in GL_CODES
    }


def assert_gl_delta(
    *,
    before,
    after,
    expected,
):
    actual = gl_delta(
        before=before,
        after=after,
    )

    assert (
        actual
        == expected
    ), (
        "\nGL DELTA MISMATCH\n"
        f"expected={expected}\n"
        f"actual={actual}"
    )


async def journal_ids_for_source(
    db: AsyncSession,
    *,
    source_kind: str,
    source_id: int,
):
    column_name = SOURCE_COLUMNS.get(
        source_kind
    )

    if column_name is None:
        raise ValueError(
            "Unsupported source kind: "
            f"{source_kind}"
        )

    rows = (
        await db.execute(
            text(
                f"""
                SELECT id
                FROM journal_entries
                WHERE company_id =
                      :company_id
                  AND {column_name} =
                      :source_id
                  AND reversal_of_id
                      IS NULL
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

    return tuple(
        int(
            row_id
        )
        for row_id in rows
    )


async def source_journal_id(
    db: AsyncSession,
    *,
    source_kind: str,
    source_id: int,
) -> int:
    ids = await journal_ids_for_source(
        db,
        source_kind=source_kind,
        source_id=source_id,
    )

    assert len(
        ids
    ) == 1, (
        "Expected exactly one original "
        f"{source_kind} journal, got {ids}"
    )

    return ids[0]


async def assert_journal_posting(
    db: AsyncSession,
    *,
    journal_entry_id: int,
    expected,
):
    header = (
        await db.execute(
            text(
                """
                SELECT
                    status,
                    reversal_of_id
                FROM journal_entries
                WHERE company_id =
                      :company_id
                  AND id =
                      :journal_entry_id
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
                "journal_entry_id": (
                    journal_entry_id
                ),
            },
        )
    ).mappings().one()

    assert (
        header["status"]
        == "posted"
    )

    assert (
        header["reversal_of_id"]
        is None
    )

    rows = (
        await db.execute(
            text(
                """
                SELECT
                    a.code,
                    SUM(jel.debit) AS debit,
                    SUM(jel.credit) AS credit
                FROM journal_entry_lines jel
                JOIN journal_entries je
                  ON je.id =
                         jel.journal_entry_id
                JOIN accounts a
                  ON a.id =
                         jel.account_id
                 AND a.company_id =
                         je.company_id
                WHERE je.company_id =
                      :company_id
                  AND je.id =
                      :journal_entry_id
                GROUP BY a.code
                ORDER BY a.code
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
                "journal_entry_id": (
                    journal_entry_id
                ),
            },
        )
    ).mappings().all()

    actual = {
        row["code"]: (
            Decimal(
                row["debit"]
            ),
            Decimal(
                row["credit"]
            ),
        )
        for row in rows
    }

    assert actual == expected, (
        "\nJOURNAL POSTING MISMATCH\n"
        f"journal={journal_entry_id}\n"
        f"expected={expected}\n"
        f"actual={actual}"
    )


async def ensure_business_period(
    db: AsyncSession,
):
    period = (
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
                  AND year =
                      :year
                  AND month =
                      :month
                FOR UPDATE
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
                "year": (
                    BUSINESS_DATE.year
                ),
                "month": (
                    BUSINESS_DATE.month
                ),
            },
        )
    ).mappings().one_or_none()

    if period is not None:
        assert (
            period["status"]
            == "open"
        )

        assert (
            period["is_locked"]
            is False
        )

        assert (
            period["start_date"]
            <= BUSINESS_DATE
            <= period["end_date"]
        )

        return int(
            period["id"]
        )

    last_day = calendar.monthrange(
        BUSINESS_DATE.year,
        BUSINESS_DATE.month,
    )[1]

    start_date = date(
        BUSINESS_DATE.year,
        BUSINESS_DATE.month,
        1,
    )

    end_date = date(
        BUSINESS_DATE.year,
        BUSINESS_DATE.month,
        last_day,
    )

    period_id = (
        await db.execute(
            text(
                """
                INSERT INTO accounting_periods (
                    company_id,
                    year,
                    month,
                    start_date,
                    end_date,
                    status,
                    is_locked,
                    created_at,
                    closed_at
                )
                VALUES (
                    :company_id,
                    :year,
                    :month,
                    :start_date,
                    :end_date,
                    'open',
                    false,
                    CURRENT_TIMESTAMP,
                    NULL
                )
                RETURNING id
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
                "year": (
                    BUSINESS_DATE.year
                ),
                "month": (
                    BUSINESS_DATE.month
                ),
                "start_date": (
                    start_date
                ),
                "end_date": (
                    end_date
                ),
            },
        )
    ).scalar_one()

    await db.flush()

    return int(
        period_id
    )


async def require_base_master_data(
    db: AsyncSession,
):
    company_ok = (
        await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM companies
                WHERE id =
                      :company_id
                  AND is_active IS TRUE
                  AND chart_of_accounts_template =
                      'general_291'
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
            },
        )
    ).scalar_one()

    assert int(
        company_ok
    ) == 1

    user_ok = (
        await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM users
                WHERE id =
                      :user_id
                  AND is_active IS TRUE
                """
            ),
            {
                "user_id": (
                    USER_ID
                ),
            },
        )
    ).scalar_one()

    assert int(
        user_ok
    ) == 1

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
                      '311',
                      '361',
                      '681',
                      '702'
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

    account_codes = {
        row["code"]
        for row in account_rows
        if (
            row["is_active"]
            and row["is_postable"]
        )
    }

    assert account_codes == {
        "311",
        "361",
        "681",
        "702",
    }

    product_id = (
        await db.execute(
            text(
                """
                SELECT id
                FROM products
                WHERE company_id =
                      :company_id
                  AND is_active IS TRUE
                ORDER BY id
                LIMIT 1
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
            },
        )
    ).scalar_one_or_none()

    assert product_id is not None

    warehouse_id = (
        await db.execute(
            text(
                """
                SELECT id
                FROM warehouses
                WHERE company_id =
                      :company_id
                  AND is_active IS TRUE
                ORDER BY id
                LIMIT 1
                """
            ),
            {
                "company_id": (
                    COMPANY_ID
                ),
            },
        )
    ).scalar_one_or_none()

    assert warehouse_id is not None

    return (
        int(
            product_id
        ),
        int(
            warehouse_id
        ),
    )


async def create_business_fixture(
    db: AsyncSession,
):
    await ensure_business_period(
        db
    )

    (
        product_id,
        warehouse_id,
    ) = await require_base_master_data(
        db
    )

    token = uuid4().hex[
        :12
    ]

    customer = Counterparty(
        company_id=COMPANY_ID,
        name=(
            "Customer Advance E2E "
            + token
        ),
        short_name=(
            "CAC-E2E-"
            + token
        ),
        counterparty_type=(
            CounterpartyType.CUSTOMER
        ),
        vat_status=(
            CounterpartyVatStatus.NON_VAT_PAYER
        ),
        default_currency_code=CURRENCY,
        payment_term_days=0,
        credit_limit=ZERO_MONEY,
        is_active=True,
    )

    db.add(
        customer
    )

    await db.flush()

    contract = Contract(
        company_id=COMPANY_ID,
        counterparty_id=customer.id,
        number=(
            "CAC-CONTRACT-"
            + token
        ),
        name=(
            "Customer Advance E2E"
        ),
        contract_type=(
            ContractType.SALES
        ),
        status=(
            ContractStatus.ACTIVE
        ),
        start_date=BUSINESS_DATE,
        end_date=None,
        currency_code=CURRENCY,
        payment_term_days=0,
        credit_limit=ZERO_MONEY,
    )

    db.add(
        contract
    )

    await db.flush()

    order = TradeDocument(
        company_id=COMPANY_ID,
        counterparty_id=customer.id,
        contract_id=contract.id,
        number=(
            "CAC-ORDER-"
            + token
        ),
        direction=(
            TradeDirection.SALE
        ),
        kind=(
            TradeDocumentKind.ORDER
        ),
        status=(
            TradeDocumentStatus.FULFILLED
        ),
        document_date=BUSINESS_DATE,
        currency_code=CURRENCY,
        payment_term_days=0,
        created_by=USER_ID,
    )

    invoice = TradeDocument(
        company_id=COMPANY_ID,
        counterparty_id=customer.id,
        contract_id=contract.id,
        number=(
            "CAC-INVOICE-"
            + token
        ),
        direction=(
            TradeDirection.SALE
        ),
        kind=(
            TradeDocumentKind.INVOICE
        ),
        status=(
            TradeDocumentStatus.CONFIRMED
        ),
        document_date=BUSINESS_DATE,
        currency_code=CURRENCY,
        payment_term_days=0,
        created_by=USER_ID,
    )

    db.add_all(
        [
            order,
            invoice,
        ]
    )

    await db.flush()

    order_line = TradeDocumentLine(
        company_id=COMPANY_ID,
        trade_document_id=order.id,
        line_number=1,
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity=TWO,
        unit_price=SIXTY,
        tax_rate_code=None,
        tax_recognition_method=None,
        tax_price_mode=None,
    )

    invoice_line = TradeDocumentLine(
        company_id=COMPANY_ID,
        trade_document_id=invoice.id,
        line_number=1,
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity=TWO,
        unit_price=SIXTY,
        tax_rate_code=None,
        tax_recognition_method=None,
        tax_price_mode=None,
    )

    db.add_all(
        [
            order_line,
            invoice_line,
        ]
    )

    await db.flush()

    open_item = CounterpartyOpenItem(
        company_id=COMPANY_ID,
        trade_document_id=invoice.id,
        counterparty_id=customer.id,
        contract_id=contract.id,
        item_type=(
            CounterpartyOpenItemType.RECEIVABLE
        ),
        status=(
            CounterpartyOpenItemStatus.OPEN
        ),
        document_date=BUSINESS_DATE,
        due_date=BUSINESS_DATE,
        currency_code=CURRENCY,
        original_amount=ONE_TWENTY,
    )

    payment = Payment(
        company_id=COMPANY_ID,
        counterparty_id=customer.id,
        contract_id=contract.id,
        number=(
            "CAC-PAYMENT-"
            + token
        ),
        direction=(
            PaymentDirection.INCOMING
        ),
        status=(
            PaymentStatus.DRAFT
        ),
        payment_date=BUSINESS_DATE,
        currency_code=CURRENCY,
        amount=ONE_TWENTY,
        external_reference=None,
        description=(
            "Customer advance chronology E2E"
        ),
        created_by=USER_ID,
    )

    db.add_all(
        [
            open_item,
            payment,
        ]
    )

    await db.flush()

    fulfillments = []

    for sequence in (
        1,
        2,
    ):
        issue = Document(
            company_id=COMPANY_ID,
            accounting_rule_id=None,
            number=(
                f"CAC-ISSUE-{sequence}-"
                + token
            ),
            document_type=(
                DocumentType.ISSUE
            ),
            document_date=(
                BUSINESS_DATE
            ),
            status=(
                DocumentStatus.POSTED
            ),
            created_by=USER_ID,
        )

        db.add(
            issue
        )

        await db.flush()

        issue_line = DocumentLine(
            document_id=issue.id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            quantity=ONE,
            price=ZERO_MONEY,
        )

        db.add(
            issue_line
        )

        await db.flush()

        fulfillment = TradeFulfillment(
            company_id=COMPANY_ID,
            trade_document_id=order.id,
            warehouse_document_id=issue.id,
            warehouse_document_type=(
                DocumentType.ISSUE
            ),
            created_by=USER_ID,
        )

        db.add(
            fulfillment
        )

        await db.flush()

        fulfillment_line = (
            TradeFulfillmentLine(
                company_id=COMPANY_ID,
                fulfillment_id=(
                    fulfillment.id
                ),
                trade_document_id=(
                    order.id
                ),
                trade_document_line_id=(
                    order_line.id
                ),
                warehouse_document_id=(
                    issue.id
                ),
                warehouse_document_line_id=(
                    issue_line.id
                ),
                product_id=(
                    product_id
                ),
                warehouse_id=(
                    warehouse_id
                ),
                quantity=ONE,
            )
        )

        db.add(
            fulfillment_line
        )

        await db.flush()

        fulfillments.append(
            (
                fulfillment,
                fulfillment_line,
            )
        )

    return {
        "customer": customer,
        "contract": contract,
        "order": order,
        "order_line": order_line,
        "invoice": invoice,
        "invoice_line": invoice_line,
        "open_item": open_item,
        "payment": payment,
        "fulfillments": tuple(
            fulfillments
        ),
    }


async def load_sales_recognition_for_allocation(
    db: AsyncSession,
    *,
    allocation_id: int,
):
    events = tuple(
        (
            await db.execute(
                select(
                    SalesRecognitionEvent
                )
                .where(
                    SalesRecognitionEvent.company_id
                    == COMPANY_ID,
                    SalesRecognitionEvent
                    .invoice_fulfillment_allocation_id
                    == allocation_id,
                )
                .order_by(
                    SalesRecognitionEvent.id
                )
            )
        ).scalars().all()
    )

    assert len(
        events
    ) == 1

    event = events[0]

    assert (
        event.reversal_of_id
        is None
    )

    return event


async def load_customer_clearing_events(
    db: AsyncSession,
    *,
    settlement_id: int,
):
    return tuple(
        (
            await db.execute(
                select(
                    CustomerAdvanceClearingEvent
                )
                .where(
                    CustomerAdvanceClearingEvent.company_id
                    == COMPANY_ID,
                    CustomerAdvanceClearingEvent
                    .payment_settlement_allocation_id
                    == settlement_id,
                )
                .order_by(
                    CustomerAdvanceClearingEvent.id
                )
            )
        ).scalars().all()
    )


@pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run the real PostgreSQL chronology test"
    ),
)
@pytest.mark.asyncio
async def test_customer_advance_payment_first_chronology_postgresql():
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
            # Incoming payment:
            #
            #     Dr 311 120
            #     Cr 681 120
            # ==================================================

            payment = await confirm_payment(
                db,
                company_id=COMPANY_ID,
                payment_id=(
                    fixture[
                        "payment"
                    ].id
                ),
                confirmed_by=USER_ID,
            )

            assert (
                enum_value(
                    payment.status
                )
                == "confirmed"
            )

            payment_journal_id = (
                await source_journal_id(
                    db,
                    source_kind="payment",
                    source_id=payment.id,
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    payment_journal_id
                ),
                expected={
                    "311": (
                        ONE_TWENTY,
                        ZERO_MONEY,
                    ),
                    "681": (
                        ZERO_MONEY,
                        ONE_TWENTY,
                    ),
                },
            )

            after_payment = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                before=gl_baseline,
                after=after_payment,
                expected={
                    "311": (
                        ONE_TWENTY,
                        ZERO_MONEY,
                    ),
                    "361": (
                        ZERO_MONEY,
                        ZERO_MONEY,
                    ),
                    "681": (
                        ZERO_MONEY,
                        ONE_TWENTY,
                    ),
                    "702": (
                        ZERO_MONEY,
                        ZERO_MONEY,
                    ),
                },
            )

            # ==================================================
            # B. COMMERCIAL RECEIVABLE SETTLEMENT = 120
            #
            # The Open Item may be commercially settled now.
            #
            # BUT economic receivable 361 does not yet exist.
            #
            # Therefore:
            #
            #     NO Dr 681 / Cr 361
            #     NO legacy settlement JournalEntry
            # ==================================================

            settlement = (
                await create_payment_settlement_allocation(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=payment.id,
                    open_item_id=(
                        fixture[
                            "open_item"
                        ].id
                    ),
                    amount=ONE_TWENTY,
                    created_by=USER_ID,
                )
            )

            assert (
                enum_value(
                    settlement.status
                )
                == "active"
            )

            settlement_journals = (
                await journal_ids_for_source(
                    db,
                    source_kind="settlement",
                    source_id=settlement.id,
                )
            )

            assert (
                settlement_journals
                == ()
            ), (
                "Legacy RECEIVABLE settlement "
                "JournalEntry must be disabled"
            )

            clearing_before_sales = (
                await load_customer_clearing_events(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            assert (
                clearing_before_sales
                == ()
            )

            after_settlement = (
                await gl_snapshot(
                    db
                )
            )

            assert (
                after_settlement
                == after_payment
            ), (
                "Commercial settlement changed GL "
                "before economic 361 existed"
            )

            payment_balance = (
                await get_payment_settlement_reconciliation(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=payment.id,
                )
            )

            assert (
                Decimal(
                    payment_balance.settled_amount
                )
                == ONE_TWENTY
            )

            assert (
                Decimal(
                    payment_balance.unallocated_amount
                )
                == ZERO_MONEY
            )

            assert (
                payment_balance.fully_allocated
                is True
            )

            open_item_balance = (
                await get_open_item_settlement_balance(
                    db,
                    company_id=COMPANY_ID,
                    open_item_id=(
                        fixture[
                            "open_item"
                        ].id
                    ),
                )
            )

            assert (
                Decimal(
                    open_item_balance.settled_amount
                )
                == ONE_TWENTY
            )

            assert (
                Decimal(
                    open_item_balance.open_amount
                )
                == ZERO_MONEY
            )

            # ==================================================
            # C. FIRST ECONOMIC SALES RECOGNITION = 60
            #
            # Sales:
            #
            #     Dr 361 60
            #     Cr 702 60
            #
            # Customer clearing:
            #
            #     Dr 681 60
            #     Cr 361 60
            # ==================================================

            (
                first_fulfillment,
                first_fulfillment_line,
            ) = fixture[
                "fulfillments"
            ][0]

            first_allocation = (
                await create_invoice_fulfillment_allocation(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=(
                        fixture[
                            "invoice"
                        ].id
                    ),
                    invoice_line_id=(
                        fixture[
                            "invoice_line"
                        ].id
                    ),
                    fulfillment_id=(
                        first_fulfillment.id
                    ),
                    fulfillment_line_id=(
                        first_fulfillment_line.id
                    ),
                    quantity=ONE,
                    created_by=USER_ID,
                )
            )

            first_recognition = (
                await load_sales_recognition_for_allocation(
                    db,
                    allocation_id=(
                        first_allocation.id
                    ),
                )
            )

            assert (
                Decimal(
                    first_recognition
                    .recognized_quantity
                )
                == ONE
            )

            assert (
                Decimal(
                    first_recognition
                    .recognized_gross_amount
                )
                == SIXTY
            )

            assert (
                Decimal(
                    first_recognition
                    .recognized_tax_amount
                )
                == ZERO_MONEY
            )

            first_sales_journal_id = (
                await source_journal_id(
                    db,
                    source_kind=(
                        "sales_recognition"
                    ),
                    source_id=(
                        first_recognition.id
                    ),
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    first_sales_journal_id
                ),
                expected={
                    "361": (
                        SIXTY,
                        ZERO_MONEY,
                    ),
                    "702": (
                        ZERO_MONEY,
                        SIXTY,
                    ),
                },
            )

            first_clearing_events = (
                await load_customer_clearing_events(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            assert len(
                first_clearing_events
            ) == 1

            first_clearing = (
                first_clearing_events[
                    0
                ]
            )

            assert (
                first_clearing.reversal_of_id
                is None
            )

            assert (
                first_clearing
                .payment_settlement_allocation_id
                == settlement.id
            )

            assert (
                first_clearing
                .sales_recognition_event_id
                == first_recognition.id
            )

            assert (
                Decimal(
                    first_clearing
                    .cleared_amount
                )
                == SIXTY
            )

            assert (
                first_clearing.currency_code
                == CURRENCY
            )

            assert (
                first_clearing.clearing_date
                >= first_recognition
                .recognition_date
            )

            assert (
                first_clearing.clearing_date
                >= payment.payment_date
            )

            first_clearing_journal_id = (
                await source_journal_id(
                    db,
                    source_kind=(
                        "customer_clearing"
                    ),
                    source_id=(
                        first_clearing.id
                    ),
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    first_clearing_journal_id
                ),
                expected={
                    "361": (
                        ZERO_MONEY,
                        SIXTY,
                    ),
                    "681": (
                        SIXTY,
                        ZERO_MONEY,
                    ),
                },
            )

            after_first_sales = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                before=gl_baseline,
                after=after_first_sales,
                expected={
                    "311": (
                        ONE_TWENTY,
                        ZERO_MONEY,
                    ),
                    "361": (
                        SIXTY,
                        SIXTY,
                    ),
                    "681": (
                        SIXTY,
                        ONE_TWENTY,
                    ),
                    "702": (
                        ZERO_MONEY,
                        SIXTY,
                    ),
                },
            )

            first_immutable_snapshot = (
                first_clearing.id,
                first_clearing
                .payment_settlement_allocation_id,
                first_clearing
                .sales_recognition_event_id,
                first_clearing
                .clearing_date,
                Decimal(
                    first_clearing
                    .cleared_amount
                ),
                first_clearing
                .currency_code,
                first_clearing
                .reversal_of_id,
            )

            # ==================================================
            # D. SECOND ECONOMIC SALES RECOGNITION = 60
            #
            # Sales:
            #
            #     Dr 361 60
            #     Cr 702 60
            #
            # Customer clearing:
            #
            #     Dr 681 60
            #     Cr 361 60
            #
            # Final:
            #
            #     total customer clearing = 120
            #     681 advance = fully cleared
            #     economic 361 = fully cleared
            # ==================================================

            (
                second_fulfillment,
                second_fulfillment_line,
            ) = fixture[
                "fulfillments"
            ][1]

            second_allocation = (
                await create_invoice_fulfillment_allocation(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=(
                        fixture[
                            "invoice"
                        ].id
                    ),
                    invoice_line_id=(
                        fixture[
                            "invoice_line"
                        ].id
                    ),
                    fulfillment_id=(
                        second_fulfillment.id
                    ),
                    fulfillment_line_id=(
                        second_fulfillment_line.id
                    ),
                    quantity=ONE,
                    created_by=USER_ID,
                )
            )

            second_recognition = (
                await load_sales_recognition_for_allocation(
                    db,
                    allocation_id=(
                        second_allocation.id
                    ),
                )
            )

            assert (
                Decimal(
                    second_recognition
                    .recognized_gross_amount
                )
                == SIXTY
            )

            second_sales_journal_id = (
                await source_journal_id(
                    db,
                    source_kind=(
                        "sales_recognition"
                    ),
                    source_id=(
                        second_recognition.id
                    ),
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    second_sales_journal_id
                ),
                expected={
                    "361": (
                        SIXTY,
                        ZERO_MONEY,
                    ),
                    "702": (
                        ZERO_MONEY,
                        SIXTY,
                    ),
                },
            )

            final_clearing_events = (
                await load_customer_clearing_events(
                    db,
                    settlement_id=(
                        settlement.id
                    ),
                )
            )

            assert len(
                final_clearing_events
            ) == 2

            assert all(
                event.reversal_of_id
                is None
                for event
                in final_clearing_events
            )

            assert {
                event.sales_recognition_event_id
                for event
                in final_clearing_events
            } == {
                first_recognition.id,
                second_recognition.id,
            }

            assert sum(
                (
                    Decimal(
                        event.cleared_amount
                    )
                    for event
                    in final_clearing_events
                ),
                ZERO_MONEY,
            ) == ONE_TWENTY

            refreshed_first = (
                final_clearing_events[
                    0
                ]
            )

            assert (
                (
                    refreshed_first.id,
                    refreshed_first
                    .payment_settlement_allocation_id,
                    refreshed_first
                    .sales_recognition_event_id,
                    refreshed_first
                    .clearing_date,
                    Decimal(
                        refreshed_first
                        .cleared_amount
                    ),
                    refreshed_first
                    .currency_code,
                    refreshed_first
                    .reversal_of_id,
                )
                == first_immutable_snapshot
            ), (
                "First clearing event mutated "
                "after second recognition"
            )

            second_clearing = next(
                event
                for event
                in final_clearing_events
                if (
                    event
                    .sales_recognition_event_id
                    == second_recognition.id
                )
            )

            second_clearing_journal_id = (
                await source_journal_id(
                    db,
                    source_kind=(
                        "customer_clearing"
                    ),
                    source_id=(
                        second_clearing.id
                    ),
                )
            )

            await assert_journal_posting(
                db,
                journal_entry_id=(
                    second_clearing_journal_id
                ),
                expected={
                    "361": (
                        ZERO_MONEY,
                        SIXTY,
                    ),
                    "681": (
                        SIXTY,
                        ZERO_MONEY,
                    ),
                },
            )

            final_gl = (
                await gl_snapshot(
                    db
                )
            )

            assert_gl_delta(
                before=gl_baseline,
                after=final_gl,
                expected={
                    "311": (
                        ONE_TWENTY,
                        ZERO_MONEY,
                    ),
                    "361": (
                        ONE_TWENTY,
                        ONE_TWENTY,
                    ),
                    "681": (
                        ONE_TWENTY,
                        ONE_TWENTY,
                    ),
                    "702": (
                        ZERO_MONEY,
                        ONE_TWENTY,
                    ),
                },
            )

            # ==================================================
            # E. FINAL COMMERCIAL / IMMUTABLE STATE
            # ==================================================

            final_settlement_journals = (
                await journal_ids_for_source(
                    db,
                    source_kind="settlement",
                    source_id=settlement.id,
                )
            )

            assert (
                final_settlement_journals
                == ()
            )

            payment_balance = (
                await get_payment_settlement_reconciliation(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=payment.id,
                )
            )

            assert (
                Decimal(
                    payment_balance.settled_amount
                )
                == ONE_TWENTY
            )

            assert (
                Decimal(
                    payment_balance.unallocated_amount
                )
                == ZERO_MONEY
            )

            open_item_balance = (
                await get_open_item_settlement_balance(
                    db,
                    company_id=COMPANY_ID,
                    open_item_id=(
                        fixture[
                            "open_item"
                        ].id
                    ),
                )
            )

            assert (
                Decimal(
                    open_item_balance.settled_amount
                )
                == ONE_TWENTY
            )

            assert (
                Decimal(
                    open_item_balance.open_amount
                )
                == ZERO_MONEY
            )

            sales_events = tuple(
                (
                    await db.execute(
                        select(
                            SalesRecognitionEvent
                        )
                        .where(
                            SalesRecognitionEvent.company_id
                            == COMPANY_ID,
                            SalesRecognitionEvent.id.in_(
                                (
                                    first_recognition.id,
                                    second_recognition.id,
                                )
                            ),
                        )
                        .order_by(
                            SalesRecognitionEvent.id
                        )
                    )
                ).scalars().all()
            )

            assert len(
                sales_events
            ) == 2

            assert all(
                event.reversal_of_id
                is None
                for event
                in sales_events
            )

            assert sum(
                (
                    Decimal(
                        event
                        .recognized_gross_amount
                    )
                    for event
                    in sales_events
                ),
                ZERO_MONEY,
            ) == ONE_TWENTY

            print(
                "PAYMENT Dr311 / Cr681 = PASS"
            )

            print(
                "COMMERCIAL SETTLEMENT 120 = PASS"
            )

            print(
                "EARLY LEGACY Dr681 / Cr361 = ABSENT"
            )

            print(
                "FIRST SALES RECOGNITION 60 = PASS"
            )

            print(
                "FIRST CUSTOMER CLEARING 60 = PASS"
            )

            print(
                "SECOND SALES RECOGNITION 60 = PASS"
            )

            print(
                "SECOND CUSTOMER CLEARING 60 = PASS"
            )

            print(
                "CUSTOMER CLEARING TOTAL 120 = PASS"
            )

            print(
                "681 FINAL ECONOMIC CLEARING = PASS"
            )

            print(
                "361 FINAL ECONOMIC CLEARING = PASS"
            )

            print(
                "COMMERCIAL SETTLEMENT REMAINS 120 = PASS"
            )

            print(
                "IMMUTABLE CLEARING HISTORY = PASS"
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

    after_counts = (
        await table_counts()
    )

    assert (
        after_counts
        == baseline_counts
    ), (
        "\nFULL E2E ROLLBACK FAILED\n"
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
