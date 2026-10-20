"""Disbursement severance guard ordering and source contracts."""

import ast
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.payroll_severance_financial_guard import (
    require_severance_financial_classification,
)


@pytest.mark.asyncio
async def test_unclassified_severance_blocks_payment():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=17)

    with pytest.raises(
        ValueError,
        match="Severance statutory classification",
    ):
        await require_severance_financial_classification(
            db,
            company_id=1,
            payroll_calculation_id=2,
            error_type=ValueError,
        )


@pytest.mark.asyncio
async def test_normal_payroll_has_no_severance_block():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)

    await require_severance_financial_classification(
        db,
        company_id=1,
        payroll_calculation_id=2,
        error_type=ValueError,
    )


def test_disbursement_guards_precede_existing_returns():
    path = Path("app/services/payroll_disbursement_service.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    pairs = (
        ("create_payroll_disbursement", "get_payroll_disbursement"),
        (
            "generate_and_post_payroll_disbursement_journal",
            "get_payroll_disbursement_journal",
        ),
    )

    for function_name, existing_name in pairs:
        functions = [
            n for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef)
            and n.name == function_name
        ]
        assert len(functions) == 1

        calls = [
            (n.lineno, n.func.id)
            for n in ast.walk(functions[0])
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
        ]

        guards = [
            line for line, name in calls
            if name == "require_severance_financial_classification"
        ]
        returns = [
            line for line, name in calls
            if name == existing_name
        ]
        assert len(guards) == len(returns) == 1
        assert guards[0] < returns[0]
