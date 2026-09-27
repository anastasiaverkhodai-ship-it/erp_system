"""Materialize opening stock and debts without generating a second GL journal.

Caller owns the transaction. Company serialization matches warehouse operations;
all validation and materialization must commit together with the opening journal.
"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, tuple_
from sqlalchemy.orm import selectinload
from app.models.opening_balance_detail import OpeningBalanceDetail
from app.models.opening_balance import OpeningBalance
from app.models.journal_entry import JournalEntry
from app.models.document import Document
from app.models.document_line import DocumentLine
from app.models.counterparty_open_item import CounterpartyOpenItem
from app.models.counterparty import Counterparty
from app.models.contract import Contract
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.models.stock_ledger import StockLedger
from app.models.payment_settlement_allocation import PaymentSettlementAllocation
from app.models.fixed_asset import FixedAsset, FixedAssetStatus
from app.models.fixed_asset_commissioning import FixedAssetCommissioning
from app.models.fixed_asset_opening_balance import FixedAssetOpeningBalance
from app.services.opening_balance_service import OpeningBalanceError, get_opening_balance
from app.services.accounting_account_roles import AccountingAccountRole as R
from app.services.accounting_account_role_resolver import resolve_company_account_roles, AccountingAccountRoleResolutionError
from app.services.accounting_period_service import ensure_period_open
from app.services.idempotency_fingerprint_service import generate_request_fingerprint
from app.services.landed_cost_inventory_lifecycle import landed_cost_inventory_operation
from app.services.posting_context import create_posting_context
from app.services.warehouse_posting_handler import WarehousePostingHandler, WarehousePostingHandlerError

ZERO=Decimal(0)




async def _validate_and_materialize_fixed_asset_openings(
    db,
    *,
    company_id,
    opening,
    journal,
    created_by,
    lines,
):
    if not lines:
        return

    asset_ids = [
        line.fixed_asset_id
        for line in lines
    ]

    assets = (
        await db.scalars(
            select(FixedAsset)
            .where(
                FixedAsset.company_id == company_id,
                FixedAsset.id.in_(asset_ids),
            )
            .order_by(FixedAsset.id)
            .with_for_update()
            .execution_options(
                populate_existing=True
            )
        )
    ).all()

    assets_by_id = {
        asset.id: asset
        for asset in assets
    }

    if set(assets_by_id) != set(asset_ids):
        raise OpeningBalanceError(
            "Invalid or foreign-company "
            "fixed asset opening source"
        )

    previous_opening = await db.scalar(
        select(FixedAssetOpeningBalance.id)
        .where(
            FixedAssetOpeningBalance.company_id
            == company_id,
            FixedAssetOpeningBalance.fixed_asset_id
            .in_(asset_ids),
        )
        .limit(1)
    )

    if previous_opening is not None:
        raise OpeningBalanceError(
            "Fixed asset already has "
            "opening-balance history"
        )

    commissioning = await db.scalar(
        select(FixedAssetCommissioning.id)
        .where(
            FixedAssetCommissioning.company_id
            == company_id,
            FixedAssetCommissioning.fixed_asset_id
            .in_(asset_ids),
            FixedAssetCommissioning.reversal_of_id
            .is_(None),
        )
        .limit(1)
    )

    if commissioning is not None:
        raise OpeningBalanceError(
            "Fixed asset opening conflicts "
            "with commissioning history"
        )

    expected = {}

    def add_expected(
        account_id,
        *,
        debit=ZERO,
        credit=ZERO,
    ):
        values = expected.setdefault(
            account_id,
            [ZERO, ZERO],
        )

        values[0] += debit
        values[1] += credit

    for line in lines:
        asset = assets_by_id[
            line.fixed_asset_id
        ]

        if asset.status not in {
            FixedAssetStatus.DRAFT,
            FixedAssetStatus.READY_FOR_COMMISSIONING,
        }:
            raise OpeningBalanceError(
                "Fixed asset opening requires "
                "draft or ready-for-commissioning "
                "status"
            )

        if (
            line.acquisition_date
            > opening.opening_date
        ):
            raise OpeningBalanceError(
                "Fixed asset acquisition date "
                "cannot follow opening date"
            )

        if (
            line.in_service_date
            > opening.opening_date
        ):
            raise OpeningBalanceError(
                "Fixed asset in-service date "
                "cannot follow opening date"
            )

        if (
            line.original_cost
            < asset.salvage_value
        ):
            raise OpeningBalanceError(
                "Fixed asset opening cost cannot "
                "be below salvage value"
            )

        add_expected(
            asset.asset_account_id,
            debit=line.original_cost,
        )

        if (
            line.accumulated_depreciation
            > ZERO
        ):
            add_expected(
                asset.accumulated_depreciation_account_id,
                credit=(
                    line.accumulated_depreciation
                ),
            )

    for account_id, values in expected.items():
        actual_debit = sum(
            (
                journal_line.debit
                for journal_line in journal.lines
                if journal_line.account_id
                == account_id
            ),
            ZERO,
        )

        actual_credit = sum(
            (
                journal_line.credit
                for journal_line in journal.lines
                if journal_line.account_id
                == account_id
            ),
            ZERO,
        )

        if (
            actual_debit,
            actual_credit,
        ) != (
            values[0],
            values[1],
        ):
            raise OpeningBalanceError(
                "Fixed asset opening detail "
                "does not exactly match GL "
                f"account {account_id}"
            )

    for line in lines:
        asset = assets_by_id[
            line.fixed_asset_id
        ]

        previous_status = (
            asset.status.value
            if hasattr(asset.status, "value")
            else str(asset.status)
        )

        db.add(
            FixedAssetOpeningBalance(
                company_id=company_id,
                opening_balance_id=opening.id,
                fixed_asset_id=asset.id,
                acquisition_date=(
                    line.acquisition_date
                ),
                in_service_date=(
                    line.in_service_date
                ),
                original_cost=(
                    line.original_cost
                ),
                accumulated_depreciation=(
                    line.accumulated_depreciation
                ),
                previous_status=(
                    previous_status
                ),
                previous_acquisition_date=(
                    asset.acquisition_date
                ),
                previous_in_service_date=(
                    asset.in_service_date
                ),
                previous_original_cost=(
                    asset.original_cost
                ),
                created_by=created_by,
            )
        )

        asset.acquisition_date = (
            line.acquisition_date
        )
        asset.in_service_date = (
            line.in_service_date
        )
        asset.original_cost = (
            line.original_cost
        )
        asset.status = (
            FixedAssetStatus.IN_SERVICE
        )

    await db.flush()


async def _reverse_fixed_asset_openings(
    db,
    *,
    company_id,
    opening_balance_id,
):
    rows = (
        await db.scalars(
            select(FixedAssetOpeningBalance)
            .where(
                FixedAssetOpeningBalance.company_id
                == company_id,
                FixedAssetOpeningBalance.opening_balance_id
                == opening_balance_id,
            )
            .order_by(
                FixedAssetOpeningBalance.id
            )
            .with_for_update()
            .execution_options(
                populate_existing=True
            )
        )
    ).all()

    if not rows:
        return

    asset_ids = [
        row.fixed_asset_id
        for row in rows
    ]

    assets = (
        await db.scalars(
            select(FixedAsset)
            .where(
                FixedAsset.company_id
                == company_id,
                FixedAsset.id.in_(
                    asset_ids
                ),
            )
            .order_by(FixedAsset.id)
            .with_for_update()
            .execution_options(
                populate_existing=True
            )
        )
    ).all()

    assets_by_id = {
        asset.id: asset
        for asset in assets
    }

    if set(assets_by_id) != set(asset_ids):
        raise OpeningBalanceError(
            "Fixed asset opening reversal "
            "source is incomplete"
        )

    for row in rows:
        asset = assets_by_id[
            row.fixed_asset_id
        ]

        if (
            asset.status
            != FixedAssetStatus.IN_SERVICE
            or asset.acquisition_date
            != row.acquisition_date
            or asset.in_service_date
            != row.in_service_date
            or asset.original_cost
            != row.original_cost
        ):
            raise OpeningBalanceError(
                "Fixed asset changed after "
                "opening; reverse later "
                "fixed-asset lifecycle first"
            )

        asset.status = FixedAssetStatus(
            row.previous_status
        )

        asset.acquisition_date = (
            row.previous_acquisition_date
        )

        asset.in_service_date = (
            row.previous_in_service_date
        )

        asset.original_cost = (
            row.previous_original_cost
        )

    await db.flush()

@landed_cost_inventory_operation(result_date=lambda opening: opening.opening_date)
async def attach_opening_details(db, *, company_id, opening_balance_id, created_by, data):
    opening=await get_opening_balance(db, company_id, opening_balance_id)
    journal=await db.scalar(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.company_id==company_id, JournalEntry.id==opening.journal_entry_id
    ).with_for_update().execution_options(populate_existing=True))
    fingerprint=generate_request_fingerprint(data.model_dump(mode='json'))
    existing=await db.scalar(select(OpeningBalanceDetail).where(
        OpeningBalanceDetail.company_id==company_id, OpeningBalanceDetail.opening_balance_id==opening.id))
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise OpeningBalanceError('Opening detail already exists with different data')
        return opening
    if created_by<=0 or journal.status!='posted':
        raise OpeningBalanceError('Detail requires a posted opening and a valid actor')
    await ensure_period_open(company_id=company_id, operation_date=opening.opening_date, db=db)
    try:
        accounts=await resolve_company_account_roles(db, company_id=company_id,
            roles=(R.INVENTORY_GOODS, R.CUSTOMER_RECEIVABLES, R.SUPPLIER_PAYABLES))
    except AccountingAccountRoleResolutionError as exc:
        raise OpeningBalanceError(str(exc)) from exc
    ids={role: account.id for role,account in accounts.items()}
    if len(set(ids.values()))!=3:
        raise OpeningBalanceError('Opening detail accounts must be distinct')
    totals={role:ZERO for role in ids}
    totals[R.INVENTORY_GOODS]=sum(((line.quantity*line.unit_cost).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        for line in data.stock), ZERO)
    for line in data.debts:
        if line.document_date>opening.opening_date:
            raise OpeningBalanceError('Original debt date cannot follow opening date')
        role=R.CUSTOMER_RECEIVABLES if line.item_type=='receivable' else R.SUPPLIER_PAYABLES
        totals[role]+=line.amount
        counterparty=await db.scalar(select(Counterparty).where(Counterparty.id==line.counterparty_id,
            Counterparty.company_id==company_id, Counterparty.is_active.is_(True)))
        if counterparty is None:
            raise OpeningBalanceError('Invalid or inactive opening counterparty')
        if line.contract_id is not None:
            # Closed legacy contracts can still have outstanding debt at cutover.
            contract=await db.scalar(select(Contract).where(Contract.id==line.contract_id,
                Contract.company_id==company_id, Contract.counterparty_id==line.counterparty_id))
            if contract is None or contract.currency_code!='UAH':
                raise OpeningBalanceError('Invalid opening contract or currency')
    for role, account_id in ids.items():
        debit=sum((line.debit for line in journal.lines if line.account_id==account_id), ZERO)
        credit=sum((line.credit for line in journal.lines if line.account_id==account_id), ZERO)
        expected_debit,expected_credit=(ZERO,totals[role]) if role==R.SUPPLIER_PAYABLES else (totals[role],ZERO)
        if (debit,credit)!=(expected_debit,expected_credit):
            raise OpeningBalanceError(f'Opening detail does not exactly match GL account {account_id}')
    if data.stock:
        for model,values in ((Product,{line.product_id for line in data.stock}),
                             (Warehouse,{line.warehouse_id for line in data.stock})):
            found=set((await db.scalars(select(model.id).where(model.company_id==company_id,
                model.id.in_(values),model.is_active.is_(True)))).all())
            if found!=values:
                raise OpeningBalanceError('Invalid or inactive opening product/warehouse')
        identities=[(line.product_id,line.warehouse_id) for line in data.stock]
        from sqlalchemy import or_
        if await db.scalar(select(StockLedger.id).join(Document,Document.id==StockLedger.document_id).where(
            StockLedger.company_id==company_id,
            tuple_(StockLedger.product_id,StockLedger.warehouse_id).in_(identities),
            or_(Document.status!='reversed',StockLedger.movement_date>opening.opening_date)).limit(1)):
            raise OpeningBalanceError('Opening stock conflicts with active or later stock history; reverse it first and use a cutover date after its reversal')
    await _validate_and_materialize_fixed_asset_openings(
        db,
        company_id=company_id,
        opening=opening,
        journal=journal,
        created_by=created_by,
        lines=data.fixed_assets,
    )
    package=OpeningBalanceDetail(company_id=company_id,opening_balance_id=opening.id,
        request_fingerprint=fingerprint,created_by=created_by)
    db.add(package)
    if data.stock:
        document=Document(company_id=company_id,number=f'OPENING-{opening.id}',document_type='receipt',
            document_date=opening.opening_date,status='posted',created_by=created_by,
            posted_at=datetime.now(timezone.utc).replace(tzinfo=None),lines=[DocumentLine(
                product_id=line.product_id,warehouse_id=line.warehouse_id,quantity=line.quantity,price=line.unit_cost
            ) for line in data.stock])
        db.add(document); await db.flush()
        package.stock_document_id=document.id
        await db.flush()
        context=create_posting_context(db,document,0,created_by)
        for line in document.lines:
            context.set_receipt_exact_valuation_amount(line.id,
                (line.quantity*line.price).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        try:
            await WarehousePostingHandler().post(context)
        except WarehousePostingHandlerError as exc:
            raise OpeningBalanceError(str(exc)) from exc
    for line in data.debts:
        db.add(CounterpartyOpenItem(company_id=company_id,opening_balance_id=opening.id,
            opening_reference=line.reference,trade_document_id=None,counterparty_id=line.counterparty_id,
            contract_id=line.contract_id,item_type=line.item_type,status='open',document_date=line.document_date,
            due_date=line.due_date,currency_code='UAH',original_amount=line.amount))
    await db.flush()
    return opening


async def create_opening_package(db, *, company_id, created_by, data):
    from app.services.opening_balance_service import create_opening_balance, post_opening_balance
    opening=await create_opening_balance(db,company_id,created_by,data.opening)
    await post_opening_balance(db,company_id,opening.id)
    return await attach_opening_details(db,company_id=company_id,opening_balance_id=opening.id,
        created_by=created_by,data=data.details)


async def reverse_opening_detail(db, *, company_id, opening_balance_id, reversal_date, reversed_by):
    package=await db.scalar(select(OpeningBalanceDetail).where(
        OpeningBalanceDetail.company_id==company_id,OpeningBalanceDetail.opening_balance_id==opening_balance_id))
    if package is None:
        return
    await _reverse_fixed_asset_openings(
        db,
        company_id=company_id,
        opening_balance_id=opening_balance_id,
    )
    items=(await db.scalars(select(CounterpartyOpenItem).where(CounterpartyOpenItem.company_id==company_id,
        CounterpartyOpenItem.opening_balance_id==opening_balance_id).with_for_update()
        .execution_options(populate_existing=True))).all()
    if items and await db.scalar(select(PaymentSettlementAllocation.id).where(
        PaymentSettlementAllocation.company_id==company_id,
        PaymentSettlementAllocation.open_item_id.in_([item.id for item in items]),
        PaymentSettlementAllocation.status=='active').limit(1)):
        raise OpeningBalanceError('Reverse opening debt settlements before reversing the opening')
    if items and await db.scalar(select(PaymentSettlementAllocation.id).where(
        PaymentSettlementAllocation.company_id==company_id,
        PaymentSettlementAllocation.open_item_id.in_([item.id for item in items]),
        PaymentSettlementAllocation.reversed_at.is_not(None),
        PaymentSettlementAllocation.reversed_at >= datetime.combine(reversal_date,datetime.max.time(),timezone.utc)).limit(1)):
        raise OpeningBalanceError('Opening reversal cannot predate settlement history')
    if package.stock_document_id is not None:
        from app.services.document_reversal import reverse_document, DocumentReversalError
        try:
            await reverse_document(db,company_id=company_id,document_id=package.stock_document_id,
                reversal_date=reversal_date,reversed_by=reversed_by)
        except DocumentReversalError as exc:
            raise OpeningBalanceError(str(exc)) from exc
    for item in items:
        item.status='cancelled'
    await db.flush()
