from contextlib import asynccontextmanager

from app.core.database import AsyncSessionLocal


@asynccontextmanager
async def transaction():
    async with AsyncSessionLocal() as session:

        try:
            yield session

            await session.commit()

        except Exception:
            await session.rollback()
            raise