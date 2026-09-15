import pytest_asyncio

from app.core.database import engine


@pytest_asyncio.fixture(autouse=True)
async def isolate_async_database_pool():
    """Never reuse an asyncpg connection in another test's event loop."""
    yield
    await engine.dispose()
