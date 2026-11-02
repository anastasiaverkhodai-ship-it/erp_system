from unittest.mock import AsyncMock

import pytest

from app.services.payroll_severance_verified_correction_service import (
    VerifiedSeveranceSourceError,
    require_no_active_severance_settlement,
)


@pytest.mark.asyncio
async def test_active_payment_blocks_correction():
    db = AsyncMock()
    db.scalar.return_value = 42

    with pytest.raises(
        VerifiedSeveranceSourceError,
        match="financial settlement correction",
    ):
        await require_no_active_severance_settlement(
            db,
            company_id=1,
            payroll_input_id=5,
        )

    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_payment_allows_existing_revision_path():
    db = AsyncMock()
    db.scalar.return_value = None

    result = await require_no_active_severance_settlement(
        db,
        company_id=1,
        payroll_input_id=5,
    )

    assert result is None
    db.scalar.assert_awaited_once()


@pytest.mark.parametrize(
    "company_id,payroll_input_id",
    [
        (0, 1),
        (1, 0),
        (-1, 1),
        (1, -1),
        (True, 1),
        (1, False),
        ("1", 1),
    ],
)
@pytest.mark.asyncio
async def test_invalid_scope_rejected(
    company_id,
    payroll_input_id,
):
    db = AsyncMock()

    with pytest.raises(VerifiedSeveranceSourceError):
        await require_no_active_severance_settlement(
            db,
            company_id=company_id,
            payroll_input_id=payroll_input_id,
        )

    db.scalar.assert_not_awaited()


def test_guard_precedes_new_revision_creation():
    import ast
    from pathlib import Path

    source = Path(
        "app/services/"
        "payroll_severance_verified_correction_service.py"
    ).read_text()

    tree = ast.parse(source)

    function = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "create_verified_severance_correction"
    )

    guard_lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "require_no_active_severance_settlement"
    ]

    revision_lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "validate_new_revision"
    ]

    assert len(guard_lines) == 1
    assert len(revision_lines) == 1
    assert guard_lines[0] < revision_lines[0]
