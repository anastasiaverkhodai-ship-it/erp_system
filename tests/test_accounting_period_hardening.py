from pathlib import Path

from app.models.accounting_period import AccountingPeriod


def _api_source() -> str:
    return Path(
        "app/api/v1/accounting_periods.py"
    ).read_text()


def _model_source() -> str:
    return Path(
        "app/models/accounting_period.py"
    ).read_text()


def test_period_model_has_state_consistency_check():
    table = AccountingPeriod.__table__

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }

    assert "ck_accounting_period_state" in checks

    expression = checks["ck_accounting_period_state"].lower()

    assert "status" in expression
    assert "is_locked" in expression
    assert "'open'" in expression
    assert "'closed'" in expression


def test_close_period_uses_row_lock():
    source = _api_source()

    start = source.index(
        "async def close_accounting_period("
    )
    end = source.index(
        "async def reopen_accounting_period("
    )

    close_source = source[start:end]

    assert ".with_for_update()" in close_source


def test_reopen_period_uses_row_lock():
    source = _api_source()

    start = source.index(
        "async def reopen_accounting_period("
    )

    reopen_source = source[start:]

    assert ".with_for_update()" in reopen_source


def test_close_requires_consistent_open_state():
    source = _api_source()

    start = source.index(
        "async def close_accounting_period("
    )
    end = source.index(
        "async def reopen_accounting_period("
    )

    close_source = source[start:end]

    assert 'period.status != "open"' in close_source
    assert "or period.is_locked" in close_source


def test_reopen_requires_consistent_closed_state():
    source = _api_source()

    start = source.index(
        "async def reopen_accounting_period("
    )

    reopen_source = source[start:]

    assert 'period.status != "closed"' in reopen_source
    assert "or not period.is_locked" in reopen_source


def test_close_transition_is_closed_and_locked():
    source = _api_source()

    start = source.index(
        "async def close_accounting_period("
    )
    end = source.index(
        "async def reopen_accounting_period("
    )

    close_source = source[start:end]

    assert 'period.status = "closed"' in close_source
    assert "period.is_locked = True" in close_source
    assert "period.closed_at =" in close_source


def test_reopen_transition_is_open_and_unlocked():
    source = _api_source()

    start = source.index(
        "async def reopen_accounting_period("
    )

    reopen_source = source[start:]

    assert 'period.status = "open"' in reopen_source
    assert "period.is_locked = False" in reopen_source
    assert "period.closed_at = None" in reopen_source


def test_period_api_remains_company_scoped():
    source = _api_source()

    assert source.count(
        "AccountingPeriod.company_id == company_id"
    ) >= 3


def test_period_hardening_does_not_add_generic_state_machine():
    source = _model_source() + "\n" + _api_source()

    assert "workflow_engine" not in source
    assert "state_machine_engine" not in source


def test_ensure_period_open_uses_for_update():
    import asyncio
    from datetime import date
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    from app.services.accounting_period_service import ensure_period_open

    class Result:
        def scalar_one_or_none(self):
            return SimpleNamespace(
                is_locked=False,
                status="open",
            )

    class Database:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return Result()

    db = Database()

    asyncio.run(
        ensure_period_open(
            company_id=1,
            operation_date=date(2026, 1, 15),
            db=db,
        )
    )

    assert db.statement is not None

    compiled = str(
        db.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert "FOR UPDATE" in compiled.upper()
