from __future__ import annotations

import importlib.util
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sys

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import engine
from app.models.trade_document import TradeDocument
from app.models.trade_value_correction_event import (
    TradeValueCorrectionEvent,
)

from app.services.invoice_tax_calculation_service import (
    create_tax_calculations_for_invoice,
)
from app.services.input_vat_fulfillment_bridge_lifecycle_service import (
    reconcile_input_vat_fulfillment_bridge_lifecycle_for_tax_calculation,
)
from app.services.purchase_value_correction_allocation_reconciliation_service import (
    reconcile_purchase_value_correction_allocations_for_event,
)
from app.services.purchase_value_correction_moving_average_lifecycle_service import (
    reconcile_and_post_purchase_value_correction_moving_average_peers,
)
from app.services.purchase_value_correction_vat_adjustment_calculation_service import (
    build_purchase_value_correction_vat_adjustment_target,
)
from app.services.purchase_value_correction_vat_adjustment_persistence_service import (
    reconcile_purchase_value_correction_vat_adjustment,
)
from app.services.purchase_value_correction_input_vat_credit_correction_calculation_service import (
    build_purchase_value_correction_input_vat_credit_target,
)
from app.services.purchase_value_correction_input_vat_credit_correction_persistence_service import (
    reconcile_purchase_value_correction_input_vat_credit_correction,
)
from app.services.purchase_value_correction_vat_accounting_lifecycle_service import (
    post_created_purchase_value_correction_input_vat_credit_correction_journals,
    post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing,
)
from app.services.tax_credit_evidence_lifecycle_service import (
    create_tax_credit_evidence_and_reconcile,
)
from app.services.tax_credit_evidence_types import (
    TaxCreditEvidenceType,
)


ZERO = Decimal("0.00")

BASE_BEFORE = Decimal("100.00")
VAT_BEFORE = Decimal("20.00")
GROSS_BEFORE = Decimal("120.00")

BASE_AFTER = Decimal("90.00")
VAT_AFTER = Decimal("18.00")
GROSS_AFTER = Decimal("108.00")


def _load_test_module(
    *,
    name: str,
    filename: str,
):
    path = (
        Path(__file__).resolve().parent
        / filename
    )

    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            f"Unable to load test support module: {path}"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    sys.modules[name] = module
    spec.loader.exec_module(module)

    return module


closure = _load_test_module(
    name="_pvc_ma_supplier_closure_support",
    filename=(
        "test_purchase_value_correction_"
        "ma_gl_supplier_closure_"
        "postgresql_chronology.py"
    ),
)

supplier = closure.supplier
ma = closure.ma

COMPANY_ID = closure.COMPANY_ID
USER_ID = closure.USER_ID


async def complete_table_counts():
    async with engine.connect() as connection:
        names = tuple(
            (
                await connection.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema =
                              current_schema()
                          AND table_type =
                              'BASE TABLE'
                        ORDER BY table_name
                        """
                    )
                )
            )
            .scalars()
            .all()
        )

        result = {}

        for name in names:
            if not (
                name.replace(
                    "_",
                    "",
                ).isalnum()
            ):
                raise AssertionError(
                    f"Unsafe table name: {name}"
                )

            result[name] = int(
                (
                    await connection.execute(
                        text(
                            f"SELECT COUNT(*) FROM {name}"
                        )
                    )
                ).scalar_one()
            )

        return result


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


async def account_net(
    db,
    *,
    code,
):
    value = (
        await db.execute(
            text(
                """
                SELECT
                    COALESCE(
                        SUM(
                            COALESCE(
                                jel.debit,
                                0
                            )
                            -
                            COALESCE(
                                jel.credit,
                                0
                            )
                        ),
                        0
                    )
                FROM journal_entry_lines jel
                JOIN journal_entries je
                  ON je.id =
                     jel.journal_entry_id
                JOIN accounts a
                  ON a.id =
                     jel.account_id
                WHERE je.company_id =
                      :company_id
                  AND je.status IN (
                      'posted',
                      'reversed'
                  )
                  AND a.code =
                      :code
                """
            ),
            {
                "company_id":
                    COMPANY_ID,
                "code":
                    code,
            },
        )
    ).scalar_one()

    return Decimal(
        str(value)
    ).quantize(
        Decimal("0.01")
    )


async def typed_journal(
    db,
    *,
    source_column,
    source_id,
):
    allowed = {
        (
            "input_vat_fulfillment_"
            "bridge_event_id"
        ),
        "tax_recognition_event_id",
        (
            "purchase_value_correction_"
            "vat_adjustment_event_id"
        ),
        (
            "purchase_value_correction_input_"
            "vat_credit_correction_event_id"
        ),
        (
            "purchase_value_correction_"
            "ma_replay_event_id"
        ),
    }

    if source_column not in allowed:
        raise AssertionError(
            f"Unsupported source: {source_column}"
        )

    rows = tuple(
        (
            await db.execute(
                text(
                    f"""
                    SELECT
                        je.id,
                        je.entry_date,
                        je.reversal_of_id,
                        a.code,
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
                      AND je.{source_column} =
                          :source_id
                    ORDER BY
                        je.id,
                        jel.line_no
                    """
                ),
                {
                    "company_id":
                        COMPANY_ID,
                    "source_id":
                        source_id,
                },
            )
        )
        .mappings()
        .all()
    )

    if not rows:
        return None

    journal_ids = {
        int(row["id"])
        for row in rows
    }

    assert len(
        journal_ids
    ) == 1

    return {
        "id":
            next(
                iter(
                    journal_ids
                )
            ),
        "entry_date":
            rows[0][
                "entry_date"
            ],
        "reversal_of_id":
            rows[0][
                "reversal_of_id"
            ],
        "amounts": {
            str(row["code"]): {
                "debit":
                    Decimal(
                        str(
                            row[
                                "debit"
                            ]
                            or 0
                        )
                    ).quantize(
                        Decimal(
                            "0.01"
                        )
                    ),
                "credit":
                    Decimal(
                        str(
                            row[
                                "credit"
                            ]
                            or 0
                        )
                    ).quantize(
                        Decimal(
                            "0.01"
                        )
                    ),
            }
            for row in rows
        },
    }


async def active_supplier_clearing(
    db,
    *,
    settlement_id,
):
    history = (
        await supplier.supplier_events(
            db,
            settlement_id=settlement_id,
        )
    )

    reversed_ids = {
        int(
            row[
                "reversal_of_id"
            ]
        )
        for row in history
        if (
            row[
                "reversal_of_id"
            ]
            is not None
        )
    }

    active = tuple(
        row
        for row in history
        if (
            row[
                "reversal_of_id"
            ]
            is None
            and int(
                row["id"]
            )
            not in reversed_ids
        )
    )

    assert len(
        active
    ) == 1

    return (
        history,
        active[0],
    )


async def pvc_vat_history(
    db,
    *,
    correction_id,
):
    return tuple(
        (
            await db.execute(
                text(
                    """
                    SELECT
                        id,
                        adjustment_date,
                        adjustment_kind,
                        adjusted_taxable_base,
                        adjusted_tax_amount,
                        reversal_of_id
                    FROM purchase_value_correction_vat_adjustment_events
                    WHERE company_id =
                          :company_id
                      AND trade_value_correction_event_id =
                          :correction_id
                    ORDER BY id
                    """
                ),
                {
                    "company_id":
                        COMPANY_ID,
                    "correction_id":
                        correction_id,
                },
            )
        )
        .mappings()
        .all()
    )


async def pvc_legal_history(
    db,
    *,
    vat_event_id,
):
    return tuple(
        (
            await db.execute(
                text(
                    """
                    SELECT
                        id,
                        adjustment_date,
                        correction_kind,
                        corrected_taxable_base,
                        corrected_tax_amount,
                        tax_credit_evidence_id,
                        reversal_of_id
                    FROM purchase_value_correction_input_vat_credit_correction_events
                    WHERE company_id =
                          :company_id
                      AND purchase_value_correction_vat_adjustment_event_id =
                          :vat_event_id
                    ORDER BY id
                    """
                ),
                {
                    "company_id":
                        COMPANY_ID,
                    "vat_event_id":
                        vat_event_id,
                },
            )
        )
        .mappings()
        .all()
    )


def delta(
    after,
    before,
):
    return (
        after
        - before
    ).quantize(
        Decimal("0.01")
    )


def test_pvc_vat_rk_contract():
    assert (
        BASE_BEFORE
        + VAT_BEFORE
        == GROSS_BEFORE
    )

    assert (
        BASE_AFTER
        + VAT_AFTER
        == GROSS_AFTER
    )

    assert (
        GROSS_BEFORE
        - GROSS_AFTER
        == Decimal("12.00")
    )


@pytest.mark.asyncio
async def test_purchase_value_correction_vat_rk_real_postgresql_d1_d5():
    """
    Real PostgreSQL chronology.

    D1
        payment                  120
        receipt base             100
        economic INPUT VAT        20
        legal INPUT VAT credit    20
        supplier clearing        120

        631 = 0
        371 = 0

    D5
        PVC gross 120 -> 108
        PVC base  100 -> 90
        PVC VAT    20 -> 18

        economic VAT:
            Dr631 / Cr644 = 2

        supplier clearing:
            old 120
            reversal 120
            replacement 108

        MA on-hand valuation:
            Dr631 / Cr281 = 10

        legal credit:
            Dr644 / Cr641 = 2

        final:
            631 = 0
            371 = +12

    Legal 641/644 correction MUST NOT alter supplier clearing.

    Second reconciliation is a complete NOOP.

    Caller transaction rollback restores exact table counts.
    """

    await engine.dispose(
        close=False
    )

    baseline_counts = (
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
                await supplier.create_business_fixture(
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

            period_end = (
                await supplier.scalar_or_none(
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
            )

            assert (
                period_end
                is not None
            )

            if d5 > period_end:
                pytest.skip(
                    "PVC VAT/RK PG chronology requires "
                    "D1..D5 inside one open period"
                )

            # --------------------------------------------------
            # Fixture normalization BEFORE economic history.
            #
            # Existing supplier fixture is 120 units @ 1.
            #
            # For VAT chronology we need:
            #
            #     base 100
            #     VAT   20
            #     gross 120
            #
            # Payment/open-item gross already equals 120.
            # --------------------------------------------------

            await db.execute(
                text(
                    """
                    UPDATE trade_document_lines
                    SET quantity =
                            100.0000,
                        unit_price =
                            1.0000
                    WHERE company_id =
                          :company_id
                      AND id =
                          :line_id
                    """
                ),
                {
                    "company_id":
                        COMPANY_ID,
                    "line_id":
                        fixture[
                            "order_line_id"
                        ],
                },
            )

            await db.execute(
                text(
                    """
                    UPDATE trade_document_lines
                    SET quantity =
                            100.0000,
                        unit_price =
                            1.0000,
                        tax_rate_code =
                            'VAT20',
                        tax_recognition_method =
                            'first_event',
                        tax_price_mode =
                            'exclusive'
                    WHERE company_id =
                          :company_id
                      AND id =
                          :line_id
                    """
                ),
                {
                    "company_id":
                        COMPANY_ID,
                    "line_id":
                        fixture[
                            "invoice_line_id"
                        ],
                },
            )

            await db.flush()

            await ma.set_company_to_moving_average(
                db
            )

            invoice = (
                await load_invoice(
                    db,
                    invoice_id=fixture[
                        "invoice_id"
                    ],
                )
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
                calculations[0]
            )

            assert Decimal(
                calculation.taxable_base
            ) == BASE_BEFORE

            assert Decimal(
                calculation.tax_amount
            ) == VAT_BEFORE

            gl_baseline = {
                code:
                    await account_net(
                        db,
                        code=code,
                    )
                for code in (
                    "281",
                    "311",
                    "371",
                    "631",
                    "641",
                    "644",
                )
            }

            # ==================================================
            # D1 A. ADVANCE PAYMENT 120
            # ==================================================

            payment = (
                await supplier.confirm_payment(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=fixture[
                        "payment_id"
                    ],
                    confirmed_by=USER_ID,
                )
            )

            settlement = (
                await supplier.create_payment_settlement_allocation(
                    db,
                    company_id=COMPANY_ID,
                    payment_id=payment.id,
                    open_item_id=fixture[
                        "open_item_id"
                    ],
                    amount=GROSS_BEFORE,
                    created_by=USER_ID,
                )
            )

            # ==================================================
            # D1 B. RECEIPT BASE 100
            #
            # Dr281 / Cr631
            # ==================================================

            receipt = (
                await supplier.execute_purchase_order_fulfillment(
                    db,
                    company_id=COMPANY_ID,
                    trade_document_id=fixture[
                        "order_id"
                    ],
                    warehouse_document_number=(
                        "PVC-VAT-PG-R-"
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
                        supplier.PurchaseOrderFulfillmentRequestLine(
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

            receipt_line_id = (
                await supplier.fulfillment_line_id(
                    db,
                    fulfillment_id=receipt.fulfillment.id,
                )
            )

            # ==================================================
            # D1 C. IFA 100
            # ==================================================

            allocation = (
                await supplier.create_invoice_fulfillment_allocation(
                    db,
                    company_id=COMPANY_ID,
                    invoice_id=fixture[
                        "invoice_id"
                    ],
                    invoice_line_id=fixture[
                        "invoice_line_id"
                    ],
                    fulfillment_id=receipt.fulfillment.id,
                    fulfillment_line_id=receipt_line_id,
                    quantity=Decimal(
                        "100.0000"
                    ),
                    created_by=USER_ID,
                )
            )

            await db.flush()

            # Ensure economic INPUT VAT bridge is reconciled
            # through its production lifecycle.
            bridge_result = (
                await reconcile_input_vat_fulfillment_bridge_lifecycle_for_tax_calculation(
                    db,
                    company_id=COMPANY_ID,
                    tax_calculation_id=calculation.id,
                    adjustment_date=d1,
                    created_by=USER_ID,
                )
            )

            await db.flush()

            bridge_ids = tuple(
                (
                    await db.execute(
                        text(
                            """
                            SELECT id
                            FROM input_vat_fulfillment_bridge_events
                            WHERE company_id =
                                  :company_id
                              AND tax_calculation_id =
                                  :tax_calculation_id
                              AND invoice_fulfillment_allocation_id =
                                  :allocation_id
                            ORDER BY id
                            """
                        ),
                        {
                            "company_id":
                                COMPANY_ID,
                            "tax_calculation_id":
                                calculation.id,
                            "allocation_id":
                                allocation.id,
                        },
                    )
                )
                .scalars()
                .all()
            )

            assert bridge_ids

            # Reconcile supplier clearing after economic VAT
            # capacity is present.
            await supplier.reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                db,
                company_id=COMPANY_ID,
                invoice_id=fixture[
                    "invoice_id"
                ],
                adjustment_date=d1,
                created_by=USER_ID,
            )

            # ==================================================
            # D1 D. LEGAL INPUT VAT CREDIT 20
            #
            # Dr641 / Cr644
            # ==================================================

            legal_credit = (
                await create_tax_credit_evidence_and_reconcile(
                    db,
                    company_id=COMPANY_ID,
                    tax_calculation_id=calculation.id,
                    evidence_type=(
                        TaxCreditEvidenceType
                        .REGISTERED_TAX_INVOICE
                    ),
                    evidence_number=(
                        "PVC-VAT-PN-"
                        + fixture[
                            "suffix"
                        ]
                    ),
                    evidence_date=d1,
                    credit_available_date=d1,
                    evidenced_taxable_base=BASE_BEFORE,
                    evidenced_tax_amount=VAT_BEFORE,
                    currency_code="UAH",
                    adjustment_date=d1,
                    created_by=USER_ID,
                )
            )

            assert len(
                legal_credit
                .recognition
                .created_events
            ) == 1

            recognition_event = (
                legal_credit
                .recognition
                .created_events[0]
            )

            recognition_je = (
                await typed_journal(
                    db,
                    source_column=(
                        "tax_recognition_event_id"
                    ),
                    source_id=recognition_event.id,
                )
            )

            assert recognition_je is not None

            assert recognition_je[
                "amounts"
            ] == {
                "641": {
                    "debit":
                        VAT_BEFORE,
                    "credit":
                        ZERO,
                },
                "644": {
                    "debit":
                        ZERO,
                    "credit":
                        VAT_BEFORE,
                },
            }

            d1_history, d1_active = (
                await active_supplier_clearing(
                    db,
                    settlement_id=settlement.id,
                )
            )

            assert Decimal(
                d1_active[
                    "cleared_amount"
                ]
            ) == GROSS_BEFORE

            d1_gl = {
                code:
                    delta(
                        await account_net(
                            db,
                            code=code,
                        ),
                        gl_baseline[
                            code
                        ],
                    )
                for code in gl_baseline
            }

            assert d1_gl[
                "281"
            ] == BASE_BEFORE

            assert d1_gl[
                "311"
            ] == -GROSS_BEFORE

            assert d1_gl[
                "371"
            ] == ZERO

            assert d1_gl[
                "631"
            ] == ZERO

            assert d1_gl[
                "641"
            ] == VAT_BEFORE

            assert d1_gl[
                "644"
            ] == ZERO

            print(
                "D1 BASE 100 + VAT 20 + "
                "CLEARING 120 = PASS"
            )

            print(
                "D1 631 = 0 / 371 = 0 = PASS"
            )

            print(
                "D1 LEGAL Dr641 / Cr644 = 20 = PASS"
            )

            # ==================================================
            # D5 A. COMMERCIAL PVC
            #
            # gross 120 -> 108
            # VAT    20 -> 18
            # base  100 -> 90
            # ==================================================

            correction = (
                TradeValueCorrectionEvent(
                    company_id=COMPANY_ID,
                    direction="purchase",
                    trade_document_id=fixture[
                        "invoice_id"
                    ],
                    trade_document_line_id=fixture[
                        "invoice_line_id"
                    ],
                    product_id=fixture[
                        "product_id"
                    ],
                    correction_date=d5,
                    original_gross_amount=GROSS_BEFORE,
                    original_tax_amount=VAT_BEFORE,
                    corrected_gross_amount=GROSS_AFTER,
                    corrected_tax_amount=VAT_AFTER,
                    currency_code="UAH",
                    reason_code=(
                        "postgres_pvc_vat_rk"
                    ),
                    created_by=USER_ID,
                    reversal_of_id=None,
                )
            )

            db.add(
                correction
            )

            await db.flush()

            allocation_result = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=correction.id,
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert len(
                allocation_result.created_events
            ) == 1

            pvc_allocation = (
                allocation_result
                .created_events[0]
            )

            assert Decimal(
                pvc_allocation
                .original_allocated_base_amount
            ) == BASE_BEFORE

            assert Decimal(
                pvc_allocation
                .corrected_allocated_base_amount
            ) == BASE_AFTER

            assert Decimal(
                pvc_allocation
                .allocated_base_delta
            ) == Decimal(
                "-10.00"
            )

            # ==================================================
            # D5 B. ECONOMIC PVC VAT
            #
            # Dr631 / Cr644 = 2
            #
            # Combined lifecycle then adjusts clearing
            # 120 -> 108.
            # ==================================================

            vat_target = (
                build_purchase_value_correction_vat_adjustment_target(
                    trade_value_correction_event_id=correction.id,
                    tax_calculation_id=calculation.id,
                    adjustment_date=d5,
                    original_taxable_base=BASE_BEFORE,
                    corrected_taxable_base=BASE_AFTER,
                    original_tax_amount=VAT_BEFORE,
                    corrected_tax_amount=VAT_AFTER,
                    currency_code="UAH",
                )
            )

            assert vat_target is not None

            vat_result = (
                await reconcile_purchase_value_correction_vat_adjustment(
                    db=db,
                    company_id=COMPANY_ID,
                    target=vat_target,
                    created_by=USER_ID,
                    adjustment_date=d5,
                )
            )

            assert len(
                vat_result.created_events
            ) == 1

            vat_event = (
                vat_result.created_events[0]
            )

            vat_journals, clearing_results = (
                await post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing(
                    db,
                    reconciliation_result=vat_result,
                    created_by=USER_ID,
                )
            )

            assert len(
                vat_journals
            ) == 1

            assert len(
                clearing_results
            ) == 1

            economic_vat_je = (
                await typed_journal(
                    db,
                    source_column=(
                        "purchase_value_correction_"
                        "vat_adjustment_event_id"
                    ),
                    source_id=vat_event.id,
                )
            )

            assert economic_vat_je is not None

            assert economic_vat_je[
                "entry_date"
            ] == d5

            assert economic_vat_je[
                "amounts"
            ] == {
                "631": {
                    "debit":
                        Decimal(
                            "2.00"
                        ),
                    "credit":
                        ZERO,
                },
                "644": {
                    "debit":
                        ZERO,
                    "credit":
                        Decimal(
                            "2.00"
                        ),
                },
            }

            after_economic_history, active_after_economic = (
                await active_supplier_clearing(
                    db,
                    settlement_id=settlement.id,
                )
            )

            assert Decimal(
                active_after_economic[
                    "cleared_amount"
                ]
            ) == GROSS_AFTER

            assert (
                active_after_economic[
                    "clearing_date"
                ]
                == d5
            )

            assert len(
                after_economic_history
            ) > len(
                d1_history
            )

            print(
                "D5 ECONOMIC VAT "
                "Dr631 / Cr644 = 2 = PASS"
            )

            print(
                "D5 SUPPLIER CLEARING "
                "120 -> 108 = PASS"
            )

            # ==================================================
            # D5 C. BASE PVC VALUATION GL
            #
            # All 100 units are still on hand:
            #
            # Dr631 / Cr281 = 10
            #
            # This closes the economic 631 effect:
            #
            # supplier clearing change   Cr631 12
            # economic VAT               Dr631  2
            # valuation                  Dr631 10
            #                             --------
            # net 631                         0
            # ==================================================

            ma_result = (
                await reconcile_and_post_purchase_value_correction_moving_average_peers(
                    db,
                    company_id=COMPANY_ID,
                    anchor_allocation_event_id=int(
                        pvc_allocation.id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert len(
                ma_result.created_events
            ) == 1

            ma_event = (
                ma_result.created_events[0]
            )

            assert (
                ma_event.effect_kind
                == "on_hand"
            )

            assert Decimal(
                ma_event.original_valuation_amount
            ) == BASE_BEFORE

            assert Decimal(
                ma_event.corrected_valuation_amount
            ) == BASE_AFTER

            ma_je = (
                await typed_journal(
                    db,
                    source_column=(
                        "purchase_value_correction_"
                        "ma_replay_event_id"
                    ),
                    source_id=ma_event.id,
                )
            )

            assert ma_je is not None

            assert ma_je[
                "amounts"
            ] == {
                "631": {
                    "debit":
                        Decimal(
                            "10.00"
                        ),
                    "credit":
                        ZERO,
                },
                "281": {
                    "debit":
                        ZERO,
                    "credit":
                        Decimal(
                            "10.00"
                        ),
                },
            }

            # ==================================================
            # D5 D. LEGAL CREDIT DECREASE
            #
            # decrease does NOT require RK evidence:
            #
            # Dr644 / Cr641 = 2
            #
            # Must NOT alter supplier clearing.
            # ==================================================

            supplier_ids_before_legal = tuple(
                int(
                    row[
                        "id"
                    ]
                )
                for row
                in after_economic_history
            )

            legal_target = (
                build_purchase_value_correction_input_vat_credit_target(
                    purchase_value_correction_vat_adjustment_event_id=vat_event.id,
                    tax_calculation_id=calculation.id,
                    adjustment_date=d5,
                    adjustment_kind="decrease",
                    adjusted_taxable_base=Decimal(
                        "10.00"
                    ),
                    adjusted_tax_amount=Decimal(
                        "2.00"
                    ),
                    recognized_credit_taxable_base=BASE_BEFORE,
                    recognized_credit_tax_amount=VAT_BEFORE,
                    currency_code="UAH",
                    tax_credit_evidence_id=None,
                    tax_credit_evidence_type=None,
                )
            )

            assert legal_target is not None

            legal_result = (
                await reconcile_purchase_value_correction_input_vat_credit_correction(
                    db=db,
                    company_id=COMPANY_ID,
                    target=legal_target,
                    created_by=USER_ID,
                    adjustment_date=d5,
                )
            )

            assert len(
                legal_result.created_events
            ) == 1

            legal_event = (
                legal_result.created_events[0]
            )

            legal_journals = (
                await post_created_purchase_value_correction_input_vat_credit_correction_journals(
                    db,
                    reconciliation_result=legal_result,
                    created_by=USER_ID,
                )
            )

            assert len(
                legal_journals
            ) == 1

            legal_je = (
                await typed_journal(
                    db,
                    source_column=(
                        "purchase_value_correction_input_"
                        "vat_credit_correction_event_id"
                    ),
                    source_id=legal_event.id,
                )
            )

            assert legal_je is not None

            assert legal_je[
                "entry_date"
            ] == d5

            assert legal_je[
                "amounts"
            ] == {
                "644": {
                    "debit":
                        Decimal(
                            "2.00"
                        ),
                    "credit":
                        ZERO,
                },
                "641": {
                    "debit":
                        ZERO,
                    "credit":
                        Decimal(
                            "2.00"
                        ),
                },
            }

            supplier_after_legal = (
                await supplier.supplier_events(
                    db,
                    settlement_id=settlement.id,
                )
            )

            assert tuple(
                int(
                    row[
                        "id"
                    ]
                )
                for row
                in supplier_after_legal
            ) == supplier_ids_before_legal

            print(
                "D5 LEGAL Dr644 / Cr641 = 2 = PASS"
            )

            print(
                "LEGAL 641/644 -> "
                "SUPPLIER CLEARING UNCHANGED = PASS"
            )

            # ==================================================
            # D5 E. FINAL GL
            # ==================================================

            final_gl = {
                code:
                    delta(
                        await account_net(
                            db,
                            code=code,
                        ),
                        gl_baseline[
                            code
                        ],
                    )
                for code in gl_baseline
            }

            assert final_gl[
                "281"
            ] == BASE_AFTER

            assert final_gl[
                "311"
            ] == -GROSS_BEFORE

            assert final_gl[
                "371"
            ] == Decimal(
                "12.00"
            )

            assert final_gl[
                "631"
            ] == ZERO

            assert final_gl[
                "641"
            ] == VAT_AFTER

            assert final_gl[
                "644"
            ] == ZERO

            print(
                "FINAL 281 = 90 = PASS"
            )

            print(
                "FINAL 631 = 0 = PASS"
            )

            print(
                "FINAL 371 = +12 = PASS"
            )

            print(
                "FINAL 641 = +18 = PASS"
            )

            print(
                "FINAL 644 = 0 = PASS"
            )

            # ==================================================
            # F. IMMUTABLE VAT/RK HISTORY
            # ==================================================

            vat_history = (
                await pvc_vat_history(
                    db,
                    correction_id=correction.id,
                )
            )

            legal_history = (
                await pvc_legal_history(
                    db,
                    vat_event_id=vat_event.id,
                )
            )

            assert len(
                vat_history
            ) == 1

            assert len(
                legal_history
            ) == 1

            assert vat_history[0][
                "reversal_of_id"
            ] is None

            assert legal_history[0][
                "reversal_of_id"
            ] is None

            assert Decimal(
                vat_history[0][
                    "adjusted_tax_amount"
                ]
            ) == Decimal(
                "2.00"
            )

            assert Decimal(
                legal_history[0][
                    "corrected_tax_amount"
                ]
            ) == Decimal(
                "2.00"
            )

            assert (
                legal_history[0][
                    "tax_credit_evidence_id"
                ]
                is None
            )

            # ==================================================
            # G. SECOND D5 RECONCILIATION = COMPLETE NOOP
            # ==================================================

            supplier_ids_before_repeat = tuple(
                int(
                    row[
                        "id"
                    ]
                )
                for row
                in supplier_after_legal
            )

            vat_history_before_repeat = (
                await pvc_vat_history(
                    db,
                    correction_id=correction.id,
                )
            )

            legal_history_before_repeat = (
                await pvc_legal_history(
                    db,
                    vat_event_id=vat_event.id,
                )
            )

            repeat_allocation = (
                await reconcile_purchase_value_correction_allocations_for_event(
                    db,
                    company_id=COMPANY_ID,
                    trade_value_correction_event_id=correction.id,
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_allocation.created_events
                == ()
            )

            repeat_vat = (
                await reconcile_purchase_value_correction_vat_adjustment(
                    db=db,
                    company_id=COMPANY_ID,
                    target=vat_target,
                    created_by=USER_ID,
                    adjustment_date=d5,
                )
            )

            assert (
                repeat_vat.created_events
                == ()
            )

            repeat_vat_journals, repeat_clearing = (
                await post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing(
                    db,
                    reconciliation_result=repeat_vat,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_vat_journals
                == ()
            )

            assert (
                repeat_clearing
                == ()
            )

            repeat_ma = (
                await reconcile_and_post_purchase_value_correction_moving_average_peers(
                    db,
                    company_id=COMPANY_ID,
                    anchor_allocation_event_id=int(
                        pvc_allocation.id
                    ),
                    adjustment_date=d5,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_ma.created_events
                == ()
            )

            repeat_legal = (
                await reconcile_purchase_value_correction_input_vat_credit_correction(
                    db=db,
                    company_id=COMPANY_ID,
                    target=legal_target,
                    created_by=USER_ID,
                    adjustment_date=d5,
                )
            )

            assert (
                repeat_legal.created_events
                == ()
            )

            repeat_legal_journals = (
                await post_created_purchase_value_correction_input_vat_credit_correction_journals(
                    db,
                    reconciliation_result=repeat_legal,
                    created_by=USER_ID,
                )
            )

            assert (
                repeat_legal_journals
                == ()
            )

            supplier_repeat = (
                await supplier.supplier_events(
                    db,
                    settlement_id=settlement.id,
                )
            )

            assert tuple(
                int(
                    row[
                        "id"
                    ]
                )
                for row
                in supplier_repeat
            ) == supplier_ids_before_repeat

            assert (
                await pvc_vat_history(
                    db,
                    correction_id=correction.id,
                )
                == vat_history_before_repeat
            )

            assert (
                await pvc_legal_history(
                    db,
                    vat_event_id=vat_event.id,
                )
                == legal_history_before_repeat
            )

            final_gl_after_repeat = {
                code:
                    delta(
                        await account_net(
                            db,
                            code=code,
                        ),
                        gl_baseline[
                            code
                        ],
                    )
                for code in gl_baseline
            }

            assert (
                final_gl_after_repeat
                == final_gl
            )

            print(
                "SECOND D5 PVC VAT/RK "
                "RECONCILE = FULL NOOP = PASS"
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

    # ==========================================================
    # H. EXACT FULL TRANSACTION ROLLBACK
    # ==========================================================

    after_counts = (
        await complete_table_counts()
    )

    assert (
        after_counts
        == baseline_counts
    ), (
        "\nPVC VAT/RK PostgreSQL rollback "
        "did not restore exact table counts.\n"
        f"before={baseline_counts}\n"
        f"after={after_counts}"
    )

    print(
        "PVC VAT/RK FULL TRANSACTION "
        "ROLLBACK = PASS"
    )

    if scenario_error is not None:
        raise scenario_error.with_traceback(
            scenario_traceback
        )
