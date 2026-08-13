from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product


async def sell_product(
    db: AsyncSession,
    product_id: int,
):
    result = await db.execute(
        select(Product)
        .where(Product.id == product_id)
        .with_for_update()
    )

    product = result.scalar_one()

    # тут перевіряємо залишок
    # тут створюємо рух -1
    # тут commit