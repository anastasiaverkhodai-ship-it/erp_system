import importlib.util
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.api.v1.tax_invoices import (
    _create_output_with_initial_registration,
)
from app.core.config import settings
from app.core.database import Base
from app.models.input_vat_credit_claim import (
    InputVatCreditClaim,
)
from app.models.order_vat_advance import (
    OrderVatAdvance,
    OrderVatAdvanceLine,
)
from app.models.payment import Payment
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.models.product_tax_classification import (
    ProductTaxClassification,
)
from app.models.tax_calculation import TaxCalculation
from app.models.tax_invoice import TaxInvoice
from app.models.tax_invoice_credit_evidence_link import (
    TaxInvoiceCreditEvidenceLink,
)
from app.models.tax_invoice_line import TaxInvoiceLine
from app.models.tax_invoice_registration_event import (
    TaxInvoiceRegistrationEvent,
)
from app.models.tax_recognition_event import (
    TaxRecognitionEvent,
)
from app.schemas.input_vat_credit_claim import (
    InputVatCreditClaimCreate,
)
from app.schemas.tax_invoice import (
    OutputTaxInvoiceCreate,
)
from app.services.input_vat_credit_claim_service import (
    create_input_vat_credit_claim,
    reverse_input_vat_credit_claim,
)
from app.services.tax_invoice_input_persistence_service import (
    InputTaxInvoiceEvidenceError,
    InputTaxInvoiceIdempotencyError,
    InputTaxInvoiceLineAttestation,
    create_input_tax_invoice,
)
from app.services.tax_invoice_output_persistence_service import (
    TaxInvoiceOutputIdempotencyError,
    TaxInvoiceOutputSourceError,
    create_output_tax_invoice,
)
from app.services.tax_invoice_registration_lifecycle_service import (
    TaxInvoiceRegistrationTransitionError,
    append_tax_invoice_registration_event,
)


RUN_POSTGRES_E2E = (
    os.getenv("RUN_POSTGRES_E2E") == "1"
)

pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run real PostgreSQL tax-invoice tests"
    ),
)


D1 = date(2026, 9, 1)
D2 = date(2026, 9, 2)
D3 = date(2026, 9, 3)


def D(value) -> Decimal:
    return Decimal(str(value))


def load_vat_seed_module():
    path = Path(__file__).with_name(
        "test_vat_first_event_postgresql.py"
    )

    spec = importlib.util.spec_from_file_location(
        "_tax_invoice_vat_seed_helpers",
        path,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "Cannot load VAT PostgreSQL seed helpers"
        )

    module = importlib.util.module_from_spec(
        spec
    )
    spec.loader.exec_module(module)

    return module


VAT_SEED = load_vat_seed_module()


async def count_rows(
    db: AsyncSession,
    model,
) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(model)
        )
        or 0
    )


@pytest.mark.asyncio
async def test_output_tax_invoice_real_postgresql():
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
    )

    schema = (
        "test_tax_invoice_output_"
        + uuid4().hex
    )

    try:
        async with engine.connect() as conn:
            tx = await conn.begin()

            try:
                await conn.execute(
                    text(
                        f'CREATE SCHEMA "{schema}"'
                    )
                )

                await conn.execute(
                    text(
                        f'SET LOCAL search_path '
                        f'TO "{schema}"'
                    )
                )

                await conn.run_sync(
                    Base.metadata.create_all
                )

                async with AsyncSession(
                    bind=conn,
                    expire_on_commit=False,
                ) as db:
                    (
                        payment_allocation,
                        fulfillment_allocation,
                    ) = await VAT_SEED.seed(
                        db,
                        False,
                        True,
                    )

                    db.add_all(
                        [
                            payment_allocation,
                            fulfillment_allocation,
                        ]
                    )

                    await db.flush()

                    await db.execute(
                        text(
                            """
                            UPDATE companies
                            SET edrpou = '12345678',
                                vat_number = '111'
                            WHERE id = 1
                            """
                        )
                    )

                    await db.execute(
                        text(
                            """
                            UPDATE counterparties
                            SET tax_number = '87654321',
                                vat_number = '222'
                            WHERE id = 1
                            """
                        )
                    )

                    await db.execute(
                        text(
                            """
                            UPDATE products
                            SET base_uom_code = 'pcs'
                            WHERE id = 1
                            """
                        )
                    )

                    await db.execute(
                        text(
                            """
                            UPDATE trade_documents
                            SET vat_policy_id = 1,
                                counterparty_vat_registration_id = 1
                            WHERE id = 1
                            """
                        )
                    )

                    db.add(
                        ProductTaxClassification(
                            company_id=1,
                            product_id=1,
                            effective_from=D1,
                            classification_kind="uktzed",
                            statutory_code="1234567890",
                            created_by=1,
                        )
                    )

                    db.add(
                        Payment(
                            id=2,
                            company_id=1,
                            counterparty_id=1,
                            number="P-ADV",
                            direction="incoming",
                            status="confirmed",
                            confirmed_at=datetime(
                                2026,
                                9,
                                1,
                                tzinfo=timezone.utc,
                            ),
                            payment_date=D1,
                            currency_code="UAH",
                            amount=D(24),
                            created_by=1,
                        )
                    )

                    db.add(
                        TaxCalculation(
                            id=2,
                            company_id=1,
                            trade_document_id=1,
                            trade_document_line_id=1,
                            product_id=1,
                            tax_type="vat",
                            direction="output",
                            tax_rate_code="VAT20",
                            tax_rate=D(".20"),
                            treatment="taxable",
                            recognition_method="first_event",
                            taxable_base=D(100),
                            tax_amount=D(20),
                            currency_code="UAH",
                            calculation_date=D1,
                        )
                    )

                    await db.flush()

                    advance = OrderVatAdvance(
                        id=1,
                        company_id=1,
                        payment_id=2,
                        order_id=1,
                        invoice_id=None,
                        status="active",
                        amount=D(24),
                        event_date=D1,
                        created_by=1,
                    )

                    db.add(advance)
                    await db.flush()

                    db.add(
                        OrderVatAdvanceLine(
                            id=1,
                            company_id=1,
                            advance_id=1,
                            tax_calculation_id=2,
                            base=D(20),
                            tax=D(4),
                            gross=D(24),
                        )
                    )

                    db.add_all(
                        [
                            TaxRecognitionEvent(
                                id=1,
                                company_id=1,
                                tax_calculation_id=1,
                                payment_settlement_allocation_id=1,
                                recognition_date=D1,
                                recognized_taxable_base=D(60),
                                recognized_tax_amount=D(12),
                                currency_code="UAH",
                                created_by=1,
                            ),
                            TaxRecognitionEvent(
                                id=2,
                                company_id=1,
                                tax_calculation_id=1,
                                invoice_fulfillment_allocation_id=1,
                                recognition_date=D2,
                                recognized_taxable_base=D(40),
                                recognized_tax_amount=D(8),
                                currency_code="UAH",
                                created_by=1,
                            ),
                            TaxRecognitionEvent(
                                id=3,
                                company_id=1,
                                tax_calculation_id=2,
                                order_vat_advance_id=1,
                                recognition_date=D1,
                                recognized_taxable_base=D(20),
                                recognized_tax_amount=D(4),
                                currency_code="UAH",
                                created_by=1,
                            ),
                        ]
                    )

                    reversed_source_payment = Payment(
                        id=3,
                        company_id=1,
                        counterparty_id=1,
                        number="P-REVERSED-SOURCE",
                        direction="incoming",
                        status="confirmed",
                        confirmed_at=datetime(
                            2026,
                            9,
                            3,
                            tzinfo=timezone.utc,
                        ),
                        payment_date=D3,
                        currency_code="UAH",
                        amount=D(12),
                        created_by=1,
                    )

                    db.add(
                        reversed_source_payment
                    )
                    await db.flush()

                    reversed_allocation = (
                        PaymentSettlementAllocation(
                            id=2,
                            company_id=1,
                            payment_id=3,
                            open_item_id=1,
                            amount=D(12),
                            created_by=1,
                        )
                    )

                    db.add(
                        reversed_allocation
                    )
                    await db.flush()

                    reversed_original = (
                        TaxRecognitionEvent(
                            id=4,
                            company_id=1,
                            tax_calculation_id=1,
                            payment_settlement_allocation_id=2,
                            recognition_date=D3,
                            recognized_taxable_base=D(10),
                            recognized_tax_amount=D(2),
                            currency_code="UAH",
                            created_by=1,
                        )
                    )

                    db.add(
                        reversed_original
                    )
                    await db.flush()

                    db.add(
                        TaxRecognitionEvent(
                            id=5,
                            company_id=1,
                            tax_calculation_id=1,
                            payment_settlement_allocation_id=2,
                            recognition_date=D3,
                            recognized_taxable_base=D(10),
                            recognized_tax_amount=D(2),
                            currency_code="UAH",
                            created_by=1,
                            reversal_of_id=4,
                        )
                    )

                    await db.flush()

                    with pytest.raises(
                        TaxInvoiceOutputSourceError,
                        match=(
                            "no original VAT recognition events"
                        ),
                    ):
                        await create_output_tax_invoice(
                            db,
                            company_id=1,
                            source_kind="settlement",
                            source_id=2,
                            document_number="PN-REVERSED",
                            created_by=1,
                        )

                    assert (
                        await count_rows(
                            db,
                            TaxInvoice,
                        )
                        == 0
                    )

                    settlement_payload = (
                        OutputTaxInvoiceCreate(
                            source_kind="settlement",
                            source_id=1,
                            document_number="PN-SETTLE",
                        )
                    )

                    with pytest.raises(
                        HTTPException,
                        match="closed",
                    ):
                        async with db.begin_nested():
                            await db.execute(
                                text(
                                    """
                                    UPDATE accounting_periods
                                    SET is_locked = true
                                    WHERE company_id = 1
                                    """
                                )
                            )

                            await (
                                _create_output_with_initial_registration(
                                    db,
                                    company_id=1,
                                    payload=settlement_payload,
                                    created_by=1,
                                )
                            )

                    assert (
                        await count_rows(
                            db,
                            TaxInvoice,
                        )
                        == 0
                    )

                    settlement_invoice = await (
                        _create_output_with_initial_registration(
                            db,
                            company_id=1,
                            payload=settlement_payload,
                            created_by=1,
                        )
                    )

                    repeated = await (
                        _create_output_with_initial_registration(
                            db,
                            company_id=1,
                            payload=settlement_payload,
                            created_by=1,
                        )
                    )

                    assert (
                        repeated.id
                        == settlement_invoice.id
                    )

                    with pytest.raises(
                        TaxInvoiceOutputIdempotencyError
                    ):
                        await create_output_tax_invoice(
                            db,
                            company_id=1,
                            source_kind="settlement",
                            source_id=1,
                            document_number=(
                                "PN-CONFLICT"
                            ),
                            created_by=1,
                        )

                    fulfillment_invoice = await (
                        _create_output_with_initial_registration(
                            db,
                            company_id=1,
                            payload=OutputTaxInvoiceCreate(
                                source_kind="fulfillment",
                                source_id=1,
                                document_number="PN-FULFILL",
                            ),
                            created_by=1,
                        )
                    )

                    advance_invoice = await (
                        _create_output_with_initial_registration(
                            db,
                            company_id=1,
                            payload=OutputTaxInvoiceCreate(
                                source_kind="order_advance",
                                source_id=1,
                                document_number="PN-ADV",
                            ),
                            created_by=1,
                        )
                    )

                    with pytest.raises(
                        TaxInvoiceOutputSourceError,
                        match="does not belong",
                    ):
                        await create_output_tax_invoice(
                            db,
                            company_id=2,
                            source_kind="settlement",
                            source_id=1,
                            document_number="PN-FOREIGN",
                            created_by=1,
                        )

                    assert (
                        await count_rows(
                            db,
                            TaxInvoice,
                        )
                        == 3
                    )

                    assert (
                        await count_rows(
                            db,
                            TaxInvoiceLine,
                        )
                        == 3
                    )

                    assert (
                        await count_rows(
                            db,
                            TaxInvoiceRegistrationEvent,
                        )
                        == 3
                    )

                    settlement_line = await db.scalar(
                        select(TaxInvoiceLine).where(
                            TaxInvoiceLine.tax_invoice_id
                            == settlement_invoice.id
                        )
                    )

                    fulfillment_line = await db.scalar(
                        select(TaxInvoiceLine).where(
                            TaxInvoiceLine.tax_invoice_id
                            == fulfillment_invoice.id
                        )
                    )

                    advance_line = await db.scalar(
                        select(TaxInvoiceLine).where(
                            TaxInvoiceLine.tax_invoice_id
                            == advance_invoice.id
                        )
                    )

                    assert settlement_line is not None
                    assert fulfillment_line is not None
                    assert advance_line is not None

                    assert Decimal(
                        settlement_line.quantity
                    ) == D("6.000000")

                    assert (
                        settlement_line.taxable_base
                        == D("60.00")
                    )

                    assert (
                        settlement_line.tax_amount
                        == D("12.00")
                    )

                    assert Decimal(
                        fulfillment_line.quantity
                    ) == D("4.000000")

                    assert (
                        fulfillment_line.taxable_base
                        == D("40.00")
                    )

                    assert (
                        fulfillment_line.tax_amount
                        == D("8.00")
                    )

                    assert Decimal(
                        advance_line.quantity
                    ) == D("2.000000")

                    assert (
                        advance_line.taxable_base
                        == D("20.00")
                    )

                    assert (
                        advance_line.tax_amount
                        == D("4.00")
                    )

                    submitted = await (
                        append_tax_invoice_registration_event(
                            db,
                            company_id=1,
                            tax_invoice_id=(
                                settlement_invoice.id
                            ),
                            status="submitted",
                            event_date=D1,
                            reference="submission-1",
                            created_by=1,
                        )
                    )

                    registered = await (
                        append_tax_invoice_registration_event(
                            db,
                            company_id=1,
                            tax_invoice_id=(
                                settlement_invoice.id
                            ),
                            status="registered",
                            event_date=D2,
                            reference="receipt-1",
                            created_by=1,
                        )
                    )

                    repeated_registration = await (
                        append_tax_invoice_registration_event(
                            db,
                            company_id=1,
                            tax_invoice_id=(
                                settlement_invoice.id
                            ),
                            status="registered",
                            event_date=D2,
                            reference="receipt-1",
                            created_by=1,
                        )
                    )

                    assert (
                        repeated_registration.id
                        == registered.id
                    )

                    assert (
                        submitted.id
                        != registered.id
                    )

                    with pytest.raises(
                        TaxInvoiceRegistrationTransitionError
                    ):
                        await (
                            append_tax_invoice_registration_event(
                                db,
                                company_id=1,
                                tax_invoice_id=(
                                    settlement_invoice.id
                                ),
                                status="submitted",
                                event_date=D3,
                                reference="late-submit",
                                created_by=1,
                            )
                        )

                    history = list(
                        (
                            await db.scalars(
                                select(
                                    TaxInvoiceRegistrationEvent
                                )
                                .where(
                                    TaxInvoiceRegistrationEvent
                                    .tax_invoice_id
                                    == settlement_invoice.id
                                )
                                .order_by(
                                    TaxInvoiceRegistrationEvent.id
                                )
                            )
                        ).all()
                    )

                    assert [
                        event.status
                        for event in history
                    ] == [
                        "prepared",
                        "submitted",
                        "registered",
                    ]

            finally:
                await tx.rollback()

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_input_tax_invoice_real_postgresql():
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
    )

    schema = (
        "test_tax_invoice_input_"
        + uuid4().hex
    )

    try:
        async with engine.connect() as conn:
            tx = await conn.begin()

            try:
                await conn.execute(
                    text(
                        f'CREATE SCHEMA "{schema}"'
                    )
                )

                await conn.execute(
                    text(
                        f'SET LOCAL search_path '
                        f'TO "{schema}"'
                    )
                )

                await conn.run_sync(
                    Base.metadata.create_all
                )

                async with AsyncSession(
                    bind=conn,
                    expire_on_commit=False,
                ) as db:
                    (
                        payment_allocation,
                        _,
                    ) = await VAT_SEED.seed(
                        db,
                        True,
                        True,
                    )

                    await db.execute(
                        text(
                            "DELETE FROM tax_credit_evidence"
                        )
                    )

                    db.add(
                        payment_allocation
                    )
                    await db.flush()

                    data = InputVatCreditClaimCreate(
                        request_key="pn-input-1-line-1",
                        tax_calculation_id=1,
                        invoice_number="PN-IN-1",
                        invoice_date=D1,
                        registration_status="registered",
                        registered_on=D2,
                        receipt_reference="receipt-input-1",
                        buyer_vat_number="111",
                        supplier_vat_number="222",
                        claim_period=D1,
                        taxable_base=D("60.00"),
                        tax_amount=D("12.00"),
                        suspensions=[],
                    )

                    claim = await (
                        create_input_vat_credit_claim(
                            db,
                            company_id=1,
                            data=data,
                            created_by=1,
                        )
                    )

                    assert isinstance(
                        claim,
                        InputVatCreditClaim,
                    )

                    line = InputTaxInvoiceLineAttestation(
                        line_number=1,
                        claim_id=claim.id,
                        description="Goods",
                        quantity=D("6"),
                        uom_code="pcs",
                        classification_kind="uktzed",
                        statutory_code="1234567890",
                    )

                    with pytest.raises(
                        InputTaxInvoiceEvidenceError,
                        match="reversed",
                    ):
                        async with db.begin_nested():
                            await reverse_input_vat_credit_claim(
                                db,
                                company_id=1,
                                claim_id=claim.id,
                                reversal_date=D3,
                                reversed_by=1,
                            )

                            await create_input_tax_invoice(
                                db,
                                company_id=1,
                                lines=[line],
                                created_by=1,
                            )

                    await db.refresh(
                        claim
                    )

                    assert (
                        claim.reversal_evidence_id
                        is None
                    )

                    invoice = await (
                        create_input_tax_invoice(
                            db,
                            company_id=1,
                            lines=[line],
                            created_by=1,
                        )
                    )

                    assert invoice.direction == "input"
                    assert (
                        invoice.source_kind
                        == "input_external"
                    )
                    assert (
                        invoice.document_number
                        == "PN-IN-1"
                    )
                    assert (
                        invoice.document_date
                        == D1
                    )

                    repeated = await (
                        create_input_tax_invoice(
                            db,
                            company_id=1,
                            lines=[line],
                            created_by=1,
                        )
                    )

                    assert repeated.id == invoice.id

                    assert (
                        await count_rows(
                            db,
                            TaxInvoice,
                        )
                        == 1
                    )

                    assert (
                        await count_rows(
                            db,
                            TaxInvoiceLine,
                        )
                        == 1
                    )

                    assert (
                        await count_rows(
                            db,
                            TaxInvoiceCreditEvidenceLink,
                        )
                        == 1
                    )

                    assert (
                        await count_rows(
                            db,
                            TaxInvoiceRegistrationEvent,
                        )
                        == 1
                    )

                    persisted_line = await db.scalar(
                        select(TaxInvoiceLine).where(
                            TaxInvoiceLine.tax_invoice_id
                            == invoice.id
                        )
                    )

                    assert persisted_line is not None
                    assert (
                        persisted_line.tax_recognition_event_id
                        is None
                    )
                    assert (
                        persisted_line.taxable_base
                        == D("60.00")
                    )
                    assert (
                        persisted_line.tax_amount
                        == D("12.00")
                    )

                    link = await db.scalar(
                        select(
                            TaxInvoiceCreditEvidenceLink
                        )
                    )

                    assert link is not None
                    assert (
                        link.tax_credit_evidence_id
                        == claim.evidence_id
                    )
                    assert (
                        link.tax_calculation_id
                        == claim.tax_calculation_id
                    )

                    registration = await db.scalar(
                        select(
                            TaxInvoiceRegistrationEvent
                        )
                    )

                    assert registration is not None
                    assert (
                        registration.status
                        == "registered"
                    )
                    assert (
                        registration.event_date
                        == D2
                    )
                    assert (
                        registration.reference
                        == "receipt-input-1"
                    )

                    with pytest.raises(
                        InputTaxInvoiceIdempotencyError,
                        match="snapshot conflicts",
                    ):
                        await create_input_tax_invoice(
                            db,
                            company_id=1,
                            lines=[
                                InputTaxInvoiceLineAttestation(
                                    line_number=1,
                                    claim_id=claim.id,
                                    description="Goods",
                                    quantity=D("6"),
                                    uom_code="pcs",
                                    classification_kind="uktzed",
                                    statutory_code="9999999999",
                                )
                            ],
                            created_by=1,
                        )

                    with pytest.raises(
                        InputTaxInvoiceEvidenceError,
                        match="do not belong",
                    ):
                        await create_input_tax_invoice(
                            db,
                            company_id=2,
                            lines=[line],
                            created_by=1,
                        )

            finally:
                await tx.rollback()

    finally:
        await engine.dispose()
