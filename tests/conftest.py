import pytest
import pytest_asyncio

from app.core.database import engine


@pytest_asyncio.fixture(autouse=True)
async def isolate_async_database_pool():
    """Never reuse an asyncpg connection in another test's event loop."""
    yield
    await engine.dispose()


@pytest.fixture
def stable_chronology_clock(monkeypatch):
    """Give D1..D5 scenarios room in a real calendar month, even on month end.

    Only opted-in scenarios activate this fixture. Their helper modules and
    application service clocks share the offset until fixture teardown.
    The wall-clock offset preserves timestamp ordering; period guards, SQL,
    balances and all financial assertions continue to run unchanged.
    """
    import sys
    from datetime import date as RealDate, datetime as RealDatetime, timedelta, timezone

    today = RealDatetime.now(timezone.utc).date()
    offset = timedelta(days=today.day - 10)

    class DatetimeType(type):
        def __instancecheck__(cls, value):
            return isinstance(value, RealDatetime)

    class DateType(type):
        def __instancecheck__(cls, value):
            return isinstance(value, RealDate)

    class ScenarioDatetime(RealDatetime, metaclass=DatetimeType):
        @classmethod
        def now(cls, tz=None):
            return RealDatetime.now(tz) - offset

        @classmethod
        def utcnow(cls):
            return RealDatetime.now(timezone.utc).replace(tzinfo=None) - offset

    class ScenarioDate(RealDate, metaclass=DateType):
        @classmethod
        def today(cls):
            return RealDate.today() - offset

    from pathlib import Path
    from types import ModuleType
    test_root = Path(__file__).resolve().parent
    pending = list(sys.modules.values())
    visited = set()
    while pending:
        module = pending.pop()
        if module is None or id(module) in visited:
            continue
        visited.add(id(module))
        filename = getattr(module, "__file__", None)
        is_test_module = filename is not None and Path(filename).resolve().parent == test_root
        if not (getattr(module, "__name__", "").startswith("app.services.") or is_test_module):
            continue
        # importlib helpers reuse aliases; old module objects can remain under
        # test.base/test.fifo even after sys.modules points at a newer helper.
        if is_test_module:
            pending.extend(value for value in vars(module).values() if isinstance(value, ModuleType))
        if getattr(module, "datetime", None) is RealDatetime:
            monkeypatch.setattr(module, "datetime", ScenarioDatetime)
        if getattr(module, "date", None) is RealDate:
            monkeypatch.setattr(module, "date", ScenarioDate)
    yield ScenarioDatetime.now(timezone.utc).date()
