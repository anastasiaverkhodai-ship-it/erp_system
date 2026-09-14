from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract
from app.models.counterparty import Counterparty
from app.models.counterparty_open_item import CounterpartyOpenItem
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine
from app.services.invoice_tax_calculation_service import (
    calculate_invoice_line_tax,
)
from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)
from app.services.payment_types import (
    PaymentSettlementAllocationStatus,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)


ZERO = Decimal("0")


class SalesCreditControlError(Exception):
    """Base sales credit-control error."""


class SalesCreditLimitExceededError(
    SalesCreditControlError
):
    pass


class SalesCreditDataIntegrityError(
    SalesCreditControlError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class SalesCreditPolicy:
    counterparty_limit: Decimal
    contract_limit: Decimal | None


@dataclass(
    frozen=True,
    slots=True,
)
class SalesCreditExposure:
    receivable_open_amount: Decimal
    residual_order_commitment: Decimal
    requested_order_amount: Decimal

    @property
    def exposure_before(self) -> Decimal:
        return (
            self.receivable_open_amount
            + self.residual_order_commitment
        )

    @property
    def exposure_after(self) -> Decimal:
        return (
            self.exposure_before
            + self.requested_order_amount
        )


@dataclass(
    frozen=True,
    slots=True,
)
class SalesCreditDecision:
    policy: SalesCreditPolicy
    counterparty_exposure: SalesCreditExposure
    contract_exposure: SalesCreditExposure | None


def calculate_sales_order_amount(
    document: TradeDocument,
) -> Decimal:
    """
    Calculate tax-inclusive Sales Order credit commitment.

    The credit commitment intentionally uses the same
    line-level VAT computation as Trade Invoice monetary
    calculation, while keeping invoice payable-total
    semantics invoice-only.

    Untaxed line:
        quantity * unit_price

    VAT EXCLUSIVE:
        base + VAT

    VAT INCLUSIVE:
        quantity * unit_price already includes VAT
    """
    if (
        document.direction != TradeDirection.SALE
        or document.kind != TradeDocumentKind.ORDER
    ):
        raise SalesCreditDataIntegrityError(
            "Credit control requires a Sales Order"
        )

    total = ZERO

    for line in document.lines:
        calculation = calculate_invoice_line_tax(
            document=document,
            line=line,
        )

        if calculation is None:
            line_amount = round_currency_amount(
                Decimal(line.quantity)
                * Decimal(line.unit_price),
                document.currency_code,
            )
        else:
            line_amount = calculation.gross_amount

        if line_amount < ZERO:
            raise SalesCreditDataIntegrityError(
                "Sales Order line amount cannot be negative"
            )

        total += line_amount

    return round_currency_amount(
        total,
        document.currency_code,
    )


def _is_limit_enabled(
    limit: Decimal,
) -> bool:
    """
    Existing master data defaults credit_limit to zero.

    Until the model has an explicit enable/disable flag,
    zero means that this particular limit is not configured.
    """
    return Decimal(limit) > ZERO


async def _lock_credit_policy(
    db: AsyncSession,
    *,
    document: TradeDocument,
) -> SalesCreditPolicy:
    counterparty = (
        await db.execute(
            select(Counterparty)
            .where(
                Counterparty.company_id
                == document.company_id,
                Counterparty.id
                == document.counterparty_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if counterparty is None:
        raise SalesCreditDataIntegrityError(
            "Sales Order counterparty not found"
        )

    counterparty_limit = Decimal(
        counterparty.credit_limit
    )

    if counterparty_limit < ZERO:
        raise SalesCreditDataIntegrityError(
            "Counterparty credit limit cannot be negative"
        )

    if document.contract_id is None:
        return SalesCreditPolicy(
            counterparty_limit=counterparty_limit,
            contract_limit=None,
        )

    contract = (
        await db.execute(
            select(Contract)
            .where(
                Contract.company_id
                == document.company_id,
                Contract.counterparty_id
                == document.counterparty_id,
                Contract.id
                == document.contract_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if contract is None:
        raise SalesCreditDataIntegrityError(
            "Sales Order contract not found"
        )

    contract_limit = Decimal(
        contract.credit_limit
    )

    if contract_limit < ZERO:
        raise SalesCreditDataIntegrityError(
            "Contract credit limit cannot be negative"
        )

    return SalesCreditPolicy(
        counterparty_limit=counterparty_limit,
        contract_limit=contract_limit,
    )


async def _get_receivable_open_amount(
    db: AsyncSession,
    *,
    company_id: int,
    counterparty_id: int,
    contract_id: int | None,
    restrict_contract: bool,
    currency_code: str,
) -> Decimal:
    conditions = [
        CounterpartyOpenItem.company_id
        == company_id,
        CounterpartyOpenItem.counterparty_id
        == counterparty_id,
        CounterpartyOpenItem.item_type
        == CounterpartyOpenItemType.RECEIVABLE,
        CounterpartyOpenItem.status.in_(
            (
                CounterpartyOpenItemStatus.OPEN,
                CounterpartyOpenItemStatus.PARTIALLY_SETTLED,
            )
        ),
    ]

    if restrict_contract:
        if contract_id is None:
            conditions.append(
                CounterpartyOpenItem.contract_id.is_(None)
            )
        else:
            conditions.append(
                CounterpartyOpenItem.contract_id
                == contract_id
            )

    open_items = (
        await db.execute(
            select(CounterpartyOpenItem)
            .where(*conditions)
            .order_by(CounterpartyOpenItem.id)
            .with_for_update()
        )
    ).scalars().all()

    if not open_items:
        return ZERO

    expected_currency = str(
        currency_code
    ).upper()

    for item in open_items:
        item_currency = str(
            item.currency_code
        ).upper()

        if item_currency != expected_currency:
            raise SalesCreditDataIntegrityError(
                "Credit exposure contains an Open Item "
                "in a different currency"
            )

    open_item_ids = tuple(
        item.id
        for item in open_items
    )

    settled_rows = (
        await db.execute(
            select(
                PaymentSettlementAllocation.open_item_id,
                func.sum(
                    PaymentSettlementAllocation.amount
                ),
            )
            .where(
                PaymentSettlementAllocation.company_id
                == company_id,
                PaymentSettlementAllocation.open_item_id.in_(
                    open_item_ids
                ),
                PaymentSettlementAllocation.status
                == PaymentSettlementAllocationStatus.ACTIVE,
            )
            .group_by(
                PaymentSettlementAllocation.open_item_id
            )
        )
    ).all()

    settled_by_open_item = {
        int(open_item_id): Decimal(amount)
        for open_item_id, amount in settled_rows
    }

    total = ZERO

    for item in open_items:
        original = Decimal(
            item.original_amount
        )

        settled = settled_by_open_item.get(
            item.id,
            ZERO,
        )

        if (
            original <= ZERO
            or settled < ZERO
            or settled > original
        ):
            raise SalesCreditDataIntegrityError(
                "Invalid receivable balance"
            )

        total += original - settled

    return total


async def _get_residual_order_commitment(
    db: AsyncSession,
    *,
    company_id: int,
    counterparty_id: int,
    contract_id: int | None,
    restrict_contract: bool,
    exclude_order_id: int,
    currency_code: str,
) -> Decimal:
    conditions = [
        TradeDocument.company_id
        == company_id,
        TradeDocument.counterparty_id
        == counterparty_id,
        TradeDocument.direction
        == TradeDirection.SALE,
        TradeDocument.kind
        == TradeDocumentKind.ORDER,
        TradeDocument.status.in_(
            (
                TradeDocumentStatus.CONFIRMED,
                TradeDocumentStatus.PARTIALLY_FULFILLED,
                TradeDocumentStatus.FULFILLED,
            )
        ),
        TradeDocument.id
        != exclude_order_id,
    ]

    if restrict_contract:
        if contract_id is None:
            conditions.append(
                TradeDocument.contract_id.is_(None)
            )
        else:
            conditions.append(
                TradeDocument.contract_id
                == contract_id
            )

    rows = (
        await db.execute(
            select(
                TradeDocumentLine.id,
                TradeDocumentLine.quantity,
                TradeDocumentLine.unit_price,
                TradeDocumentLine.tax_rate_code,
                TradeDocumentLine.tax_recognition_method,
                TradeDocumentLine.tax_price_mode,
                TradeDocument.document_date,
                TradeDocument.currency_code,
                func.coalesce(
                    func.sum(
                        InvoiceFulfillmentAllocation.quantity
                    ).filter(
                        InvoiceFulfillmentAllocation.status
                        == InvoiceFulfillmentAllocationStatus.ACTIVE
                    ),
                    ZERO,
                ),
            )
            .select_from(TradeDocument)
            .join(
                TradeDocumentLine,
                (
                    TradeDocumentLine.company_id
                    == TradeDocument.company_id
                )
                & (
                    TradeDocumentLine.trade_document_id
                    == TradeDocument.id
                ),
            )
            .outerjoin(
                InvoiceFulfillmentAllocation,
                (
                    InvoiceFulfillmentAllocation.company_id
                    == TradeDocumentLine.company_id
                )
                & (
                    InvoiceFulfillmentAllocation.order_line_id
                    == TradeDocumentLine.id
                ),
            )
            .where(*conditions)
            .group_by(
                TradeDocumentLine.id,
                TradeDocumentLine.quantity,
                TradeDocumentLine.unit_price,
                TradeDocumentLine.tax_rate_code,
                TradeDocumentLine.tax_recognition_method,
                TradeDocumentLine.tax_price_mode,
                TradeDocument.document_date,
                TradeDocument.currency_code,
            )
        )
    ).all()

    total = ZERO
    expected_currency = str(
        currency_code
    ).upper()

    for (
        line_id,
        ordered_quantity,
        unit_price,
        tax_rate_code,
        tax_recognition_method,
        tax_price_mode,
        document_date,
        row_currency_code,
        invoiced_quantity,
    ) in rows:
        row_currency = str(
            row_currency_code
        ).upper()

        if row_currency != expected_currency:
            raise SalesCreditDataIntegrityError(
                "Credit exposure contains a Sales Order "
                "commitment in a different currency"
            )
        ordered = Decimal(
            ordered_quantity
        )

        invoiced = Decimal(
            invoiced_quantity
        )

        if invoiced < ZERO or invoiced > ordered:
            raise SalesCreditDataIntegrityError(
                "Invoice allocation quantity exceeds "
                "Sales Order quantity: "
                f"line_id={line_id}"
            )

        residual_quantity = (
            ordered - invoiced
        )

        if residual_quantity == ZERO:
            continue

        residual_document = type(
            "_CreditCommitmentDocument",
            (),
            {
                "document_date": document_date,
                "currency_code": row_currency_code,
            },
        )()

        residual_line = type(
            "_CreditCommitmentLine",
            (),
            {
                "quantity": residual_quantity,
                "unit_price": Decimal(unit_price),
                "tax_rate_code": tax_rate_code,
                "tax_recognition_method": (
                    tax_recognition_method
                ),
                "tax_price_mode": tax_price_mode,
            },
        )()

        calculation = calculate_invoice_line_tax(
            document=residual_document,
            line=residual_line,
        )

        if calculation is None:
            residual_amount = round_currency_amount(
                residual_quantity
                * Decimal(unit_price),
                row_currency_code,
            )
        else:
            residual_amount = calculation.gross_amount

        if residual_amount < ZERO:
            raise SalesCreditDataIntegrityError(
                "Residual Sales Order commitment "
                "cannot be negative"
            )

        total += residual_amount

    return total


async def _build_exposure(
    db: AsyncSession,
    *,
    document: TradeDocument,
    restrict_contract: bool,
) -> SalesCreditExposure:
    receivable_open_amount = (
        await _get_receivable_open_amount(
            db,
            company_id=document.company_id,
            counterparty_id=document.counterparty_id,
            contract_id=document.contract_id,
            restrict_contract=restrict_contract,
            currency_code=document.currency_code,
        )
    )

    residual_order_commitment = (
        await _get_residual_order_commitment(
            db,
            company_id=document.company_id,
            counterparty_id=document.counterparty_id,
            contract_id=document.contract_id,
            restrict_contract=restrict_contract,
            exclude_order_id=document.id,
            currency_code=document.currency_code,
        )
    )

    return SalesCreditExposure(
        receivable_open_amount=(
            receivable_open_amount
        ),
        residual_order_commitment=(
            residual_order_commitment
        ),
        requested_order_amount=(
            calculate_sales_order_amount(document)
        ),
    )


async def get_sales_credit_decision(
    db: AsyncSession,
    *,
    document: TradeDocument,
) -> SalesCreditDecision:
    policy = await _lock_credit_policy(
        db,
        document=document,
    )

    counterparty_exposure = await _build_exposure(
        db,
        document=document,
        restrict_contract=False,
    )

    contract_exposure = None

    if document.contract_id is not None:
        contract_exposure = await _build_exposure(
            db,
            document=document,
            restrict_contract=True,
        )

    return SalesCreditDecision(
        policy=policy,
        counterparty_exposure=counterparty_exposure,
        contract_exposure=contract_exposure,
    )


def _raise_limit_exceeded(
    *,
    scope: str,
    limit: Decimal,
    exposure: SalesCreditExposure,
) -> None:
    raise SalesCreditLimitExceededError(
        "Sales credit limit exceeded: "
        f"scope={scope}, "
        f"limit={limit}, "
        f"exposure_before={exposure.exposure_before}, "
        f"order_amount={exposure.requested_order_amount}, "
        f"exposure_after={exposure.exposure_after}"
    )


async def enforce_sales_credit_limit(
    db: AsyncSession,
    *,
    document: TradeDocument,
) -> SalesCreditDecision:
    decision = await get_sales_credit_decision(
        db,
        document=document,
    )

    counterparty_limit = (
        decision.policy.counterparty_limit
    )

    if (
        _is_limit_enabled(counterparty_limit)
        and decision.counterparty_exposure.exposure_after
        > counterparty_limit
    ):
        _raise_limit_exceeded(
            scope="counterparty",
            limit=counterparty_limit,
            exposure=decision.counterparty_exposure,
        )

    contract_limit = (
        decision.policy.contract_limit
    )

    if (
        contract_limit is not None
        and _is_limit_enabled(contract_limit)
    ):
        contract_exposure = (
            decision.contract_exposure
        )

        if contract_exposure is None:
            raise SalesCreditDataIntegrityError(
                "Contract exposure is missing"
            )

        if (
            contract_exposure.exposure_after
            > contract_limit
        ):
            _raise_limit_exceeded(
                scope="contract",
                limit=contract_limit,
                exposure=contract_exposure,
            )

    return decision
