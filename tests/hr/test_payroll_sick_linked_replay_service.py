"""Unit contracts for real database-backed linked sick replay."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from decimal import Decimal as D

import pytest

from test_payroll_sick_payroll_replay import scenario

from app.services.payroll_sick_linked_replay_service import (
    preview_linked_sick_payroll_replay,
)
from app.services.payroll_sick_payroll_replay import (
    SickPayrollReplayError,
)


class FakeScalarCollection:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


def database(args):
    calc = args["calculation"]
    old = args["original_sick"]
    new = args["replacement_sick"]
    period = args["period"]
    leave = args["leave"]

    # Only SQLAlchemy query execution is substituted.
    # The production payroll replay algorithm runs unchanged.
    db = NS(
        scalar=AsyncMock(
            side_effect=[calc, old, new, period, leave]
        ),
        scalars=AsyncMock(
            return_value=FakeScalarCollection(args["lines"])
        ),
    )
    return db


@pytest.mark.asyncio
async def test_linked_replay_positive():
    args = scenario("1500")
    calc = args["calculation"]
    calc.id = 99

    old = args["original_sick"]
    new = args["replacement_sick"]

    db = database(args)

    result = await preview_linked_sick_payroll_replay(
        db,
        company_id=calc.company_id,
        original_payroll_id=calc.id,
        original_sick_id=old.id,
        replacement_sick_id=new.id,
    )

    assert result.original_gross == calc.gross_amount
    assert result.gross_delta == D("500")
    assert result.replacement_gross == calc.gross_amount + D("500")
    assert result.replacement_sick_id == new.id
    assert len(result.lines) == len(args["lines"])
    assert db.scalar.await_count == 5
    assert db.scalars.await_count == 1


@pytest.mark.asyncio
async def test_wrong_contract_rejected_before_payroll_replay():
    args = scenario("1500")
    calc = args["calculation"]
    calc.id = 99

    old = args["original_sick"]
    new = args["replacement_sick"]
    new.employment_contract_id += 1

    db = database(args)

    with pytest.raises(
        SickPayrollReplayError,
        match="contract mismatch",
    ):
        await preview_linked_sick_payroll_replay(
            db,
            company_id=calc.company_id,
            original_payroll_id=calc.id,
            original_sick_id=old.id,
            replacement_sick_id=new.id,
        )

    assert db.scalars.await_count == 0


@pytest.mark.asyncio
async def test_missing_original_payroll_rejected():
    args = scenario("1500")
    db = NS(scalar=AsyncMock(return_value=None))

    with pytest.raises(
        SickPayrollReplayError,
        match="Original payroll calculation not found",
    ):
        await preview_linked_sick_payroll_replay(
            db,
            company_id=1,
            original_payroll_id=99,
            original_sick_id=10,
            replacement_sick_id=11,
        )
