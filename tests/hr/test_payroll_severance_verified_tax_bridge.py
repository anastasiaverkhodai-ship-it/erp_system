from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.payroll_severance_verified_tax_bridge import (
    require_exclusive_severance,
    load_severance_only_financial_snapshot,
    SeveranceTaxSourceError,
)


def split(ordinary, severance, ids=(1,)):
    return NS(
        ordinary_gross=D(ordinary),
        severance_gross=D(severance),
        severance_source_ids=ids,
    )


def test_pure_severance_can_proceed():
    assert require_exclusive_severance(
        split("0.00", "10000.00")
    ).severance_gross == D("10000.00")


@pytest.mark.parametrize(
    "ordinary,severance,ids",
    [
        ("3040.00", "10000.00", (1,)),
        ("0.00", "0.00", ()),
        ("100.00", "0.00", ()),
        ("0.00", "100.00", ()),
    ],
)
def test_unsupported_source_fails_closed(
    ordinary, severance, ids
):
    with pytest.raises(
        SeveranceTaxSourceError,
        match="requires independent",
    ):
        require_exclusive_severance(
            split(ordinary, severance, ids)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id,calculation_id",
    [(0, 1), (1, 0), (-1, 1), (True, 1), (1, False)],
)
async def test_invalid_identity_does_not_query_database(
    company_id, calculation_id
):
    db = MagicMock()
    db.scalar = AsyncMock()

    with pytest.raises(SeveranceTaxSourceError):
        await load_severance_only_financial_snapshot(
            db,
            company_id=company_id,
            calculation_id=calculation_id,
            income_date=None,
            policies=(),
        )
    db.scalar.assert_not_awaited()
