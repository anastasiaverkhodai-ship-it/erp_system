from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract
from app.models.counterparty import Counterparty
from app.models.price_type import PriceType
from app.services.price_types import PriceKind


class SalesCommercialPolicyError(Exception):
    """Base commercial-policy error."""


class SalesCommercialPolicyConfigurationError(
    SalesCommercialPolicyError
):
    """Commercial policy cannot be safely resolved."""


@dataclass(frozen=True)
class ResolvedSalesCommercialPolicy:
    price_type_code: str | None


async def resolve_sales_commercial_policy(
    db: AsyncSession,
    *,
    company_id: int,
    counterparty_id: int,
    contract_id: int | None,
    explicit_price_type_code: str | None,
    explicit_unit_price: bool,
) -> ResolvedSalesCommercialPolicy:
    """
    Select the effective sales PriceType code.

    Precedence:
      1. explicit manual price
      2. explicit master price type
      3. Contract default
      4. Counterparty default
      5. legacy no-default behavior

    The service selects policy only. It does not resolve
    ProductPrice, mutate historical TradeDocumentLine snapshots,
    commit/rollback, or create accounting/VAT/warehouse evidence.
    """

    if explicit_unit_price:
        return ResolvedSalesCommercialPolicy(
            price_type_code=None,
        )

    if explicit_price_type_code is not None:
        return ResolvedSalesCommercialPolicy(
            price_type_code=(
                explicit_price_type_code.strip().upper()
            ),
        )

    counterparty = (
        await db.execute(
            select(Counterparty).where(
                Counterparty.company_id == company_id,
                Counterparty.id == counterparty_id,
            )
        )
    ).scalar_one_or_none()

    if counterparty is None:
        raise SalesCommercialPolicyConfigurationError(
            "Counterparty not found for sales commercial policy"
        )

    contract = None

    if contract_id is not None:
        contract = (
            await db.execute(
                select(Contract).where(
                    Contract.company_id == company_id,
                    Contract.id == contract_id,
                    Contract.counterparty_id == counterparty_id,
                )
            )
        ).scalar_one_or_none()

        if contract is None:
            raise SalesCommercialPolicyConfigurationError(
                "Contract not found for sales commercial policy"
            )

    selected_code: str | None = None

    if (
        contract is not None
        and contract.default_sales_price_type_code is not None
    ):
        selected_code = (
            contract.default_sales_price_type_code
        )
    elif (
        counterparty.default_sales_price_type_code is not None
    ):
        selected_code = (
            counterparty.default_sales_price_type_code
        )

    if selected_code is None:
        return ResolvedSalesCommercialPolicy(
            price_type_code=None,
        )

    selected_code = selected_code.strip().upper()

    price_type = (
        await db.execute(
            select(PriceType).where(
                PriceType.company_id == company_id,
                PriceType.code == selected_code,
            )
        )
    ).scalar_one_or_none()

    if price_type is None:
        raise SalesCommercialPolicyConfigurationError(
            "Default sales price type not found"
        )

    if price_type.kind != PriceKind.SALES.value:
        raise SalesCommercialPolicyConfigurationError(
            "Default price type must be a sales price type"
        )

    return ResolvedSalesCommercialPolicy(
        price_type_code=selected_code,
    )
