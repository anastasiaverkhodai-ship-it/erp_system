"""Candidate batch safety and unchanged payroll behavior, without database writes."""

from contextlib import nullcontext
from datetime import date, datetime, timezone
from decimal import Decimal
from itertools import permutations
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.payroll_deduction_aggregate_safety import (
    DeductionAggregateItem,
    PayrollAggregateSafetyError,
    assess_payroll_deduction_aggregate,
)

D = Decimal
DAY = date(2026, 10, 12)


def item(instruction_id=10, requested="1000", withheld="0"):
    return DeductionAggregateItem(instruction_id, D(requested), D(withheld))


def instruction(instruction_id=10, **changes):
    values = dict(id=instruction_id, company_id=7, employment_contract_id=3,
                  currency_code="UAH", effective_from=DAY, effective_to=DAY)
    values.update(changes)
    return SimpleNamespace(**values)


def evidence(category="enforcement_other", **changes):
    values = dict(legal_category=category, document_reference="Court order 123",
                  verification_status="verified", verified_by=5,
                  verified_at=datetime(2026, 10, 12, tzinfo=timezone.utc))
    values.update(changes)
    return SimpleNamespace(**values)


def database(*rows):
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=rows)
    db.no_autoflush = nullcontext()
    return db


async def assess(db, items=(item(),), **changes):
    values = dict(company_id=7, employment_contract_id=3, effective_date=DAY,
                  currency_code="UAH", statutory_net=D("10000"), items=items)
    values.update(changes)
    return await assess_payroll_deduction_aggregate(db, **values)


def assert_read_only(db):
    for name in ("add", "add_all", "delete", "flush", "commit", "rollback", "execute"):
        getattr(db, name).assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("category,amount,cap", [
    ("employer_statutory", "1000", "2000"),
    ("enforcement_other", "2000", "5000"),
    ("enforcement_special", "2500", "5000"),
    ("minor_child_alimony", "3500", "7000"),
])
async def test_homogeneous_batches(category, amount, cap):
    db = database(instruction(10), evidence(category), instruction(11), evidence(category))
    result = await assess(db, (item(10, amount), item(11, amount)))
    assert result.combined_total == D(amount) * 2
    assert result.aggregate_maximum == D(cap)
    assert result.remaining_capacity == D(cap) - D(amount) * 2
    assert result.authorizes_payroll is False
    assert_read_only(db)


@pytest.mark.asyncio
async def test_prior_withholding_and_exact_aggregate_boundary():
    db = database(instruction(10), evidence(), instruction(11), evidence(), instruction(12), evidence())
    result = await assess(db, (item(10, "1000", "1000"), item(11, "1000", "1000"), item(12)))
    assert result.requested_total == D("3000")
    assert result.already_withheld_total == D("2000")
    assert result.combined_total == D("5000")
    assert result.remaining_capacity == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("items,match", [
    ((item(10, "2000"), item(11, "2000"), item(12, "1000.01")), "aggregate"),
    ((item(10, "2000.01"),), "individual"),
    ((item(10, "1000", "1000.01"),), "individual"),
    ((item(10, "0", "2000.01"),), "individual"),
    ((item(10, "0", "2000"), item(11, "0", "2000"), item(12, "0", "1000.01")), "aggregate"),
])
async def test_overages_reject_whole_batch(items, match):
    db = database(*(row for entry in items for row in (instruction(entry.payroll_deduction_instruction_id), evidence())))
    with pytest.raises(PayrollAggregateSafetyError, match=match):
        await assess(db, items)
    assert_read_only(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("categories", list(permutations([
    "employer_statutory", "enforcement_other", "enforcement_special", "minor_child_alimony",
], 2)))
async def test_all_mixed_categories_rejected_even_for_zero_amounts(categories):
    db = database(instruction(10), evidence(categories[0]), instruction(11), evidence(categories[1]))
    with pytest.raises(PayrollAggregateSafetyError, match="Mixed"):
        await assess(db, (item(10, "0", "0"), item(11, "0", "0")))
    assert_read_only(db)


@pytest.mark.asyncio
async def test_order_does_not_allocate_priority_or_change_total():
    entries = (item(10, "1000", "1000"), item(11, "2000"), item(12))
    results = []
    for order in permutations(entries):
        db = database(*(row for entry in order for row in (instruction(entry.payroll_deduction_instruction_id), evidence())))
        results.append(await assess(db, order))
    assert all(result == results[0] for result in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_evidence", [
    None, evidence(verification_status="unverified"), evidence(verification_status="rejected"),
    evidence(category="unknown"), evidence(document_reference=" "),
    evidence(verified_by=None), evidence(verified_at=None),
])
async def test_invalid_evidence_on_later_item_returns_no_partial_success(bad_evidence):
    db = database(instruction(10), evidence(), instruction(11), bad_evidence)
    with pytest.raises(PayrollAggregateSafetyError):
        await assess(db, (item(10), item(11)))
    assert_read_only(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"company_id": 8}, {"employment_contract_id": 4}, {"id": 99},
    {"currency_code": "EUR"}, {"effective_from": date(2026, 10, 13)},
    {"effective_to": date(2026, 10, 11)},
])
async def test_scope_currency_dates(changes):
    db = database(instruction(**changes))
    with pytest.raises(PayrollAggregateSafetyError, match="scope"):
        await assess(db)
    assert db.scalar.await_count == 1
    assert_read_only(db)


@pytest.mark.asyncio
async def test_missing_instruction_is_scoped_before_evidence_lookup():
    db = database(None)
    with pytest.raises(PayrollAggregateSafetyError, match="not found"):
        await assess(db)
    query = str(db.scalar.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "payroll_deduction_instructions.company_id = 7" in query
    assert "payroll_deduction_instructions.employment_contract_id = 3" in query
    assert "payroll_deduction_instructions.id = 10" in query
    assert db.scalar.await_count == 1


@pytest.mark.asyncio
async def test_evidence_query_is_bound_to_company_and_instruction():
    db = database(instruction(), evidence())
    await assess(db)
    query = str(db.scalar.await_args_list[1].args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "payroll_deduction_legal_evidence.company_id = 7" in query
    assert "payroll_deduction_legal_evidence.payroll_deduction_instruction_id = 10" in query


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["requested_amount", "already_withheld", "statutory_net"])
@pytest.mark.parametrize("value", [D("-0.001"), D("-1"), D("NaN"), D("sNaN"), D("Infinity"), D("0.001"), D("1e100"), True, 1.0, "1", None])
async def test_invalid_money_rejected_before_read(field, value):
    db = database()
    changes = {"statutory_net": value} if field == "statutory_net" else {
        "items": (DeductionAggregateItem(10, **{field: value, **({"requested_amount": D("1")} if field == "already_withheld" else {})}),)
    }
    with pytest.raises(PayrollAggregateSafetyError):
        await assess(db, **changes)
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"company_id": True}, {"company_id": 0}, {"company_id": "7"},
    {"employment_contract_id": -1}, {"employment_contract_id": None},
    {"currency_code": "EUR"}, {"effective_date": "2026-10-12"},
    {"items": [item()]}, {"items": (object(),)},
    {"items": (item(), item())}, {"items": (item(True),)}, {"items": (item(0),)},
])
async def test_invalid_snapshot_rejected_before_read(changes):
    db = database()
    with pytest.raises(PayrollAggregateSafetyError):
        await assess(db, **changes)
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_snapshot_and_zero_net_are_non_authorizing():
    db = database()
    result = await assess(db, (), statutory_net=D("0"))
    assert result.category is None
    assert result.combined_total == result.remaining_capacity == 0
    assert result.authorizes_payroll is False
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_net_rejects_positive_deduction():
    db = database(instruction(), evidence())
    with pytest.raises(PayrollAggregateSafetyError, match="individual"):
        await assess(db, statutory_net=D("0"))


@pytest.mark.asyncio
async def test_reads_run_with_autoflush_disabled():
    db = database(instruction(), evidence())
    active = False

    class NoAutoflush:
        def __enter__(self):
            nonlocal active
            active = True

        def __exit__(self, *args):
            nonlocal active
            active = False

    rows = iter([instruction(), evidence()])

    async def read(query):
        assert active
        return next(rows)

    db.no_autoflush = NoAutoflush()
    db.scalar.side_effect = read
    await assess(db)
    assert active is False
    assert_read_only(db)


@pytest.mark.asyncio
async def test_unapproved_deduction_cannot_enter_payroll():
    from app.services.payroll_deduction_result_service import (
        PayrollDeductionResultConflictError,
        calculate_payroll_deduction_result,
    )

    db = database(
        7,
        None,
        SimpleNamespace(
            id=4,
            payroll_period_id=2,
            employment_contract_id=3,
        ),
        SimpleNamespace(
            id=5,
            net_amount=D("10000"),
            currency_code="UAH",
        ),
        SimpleNamespace(end_date=DAY),
        None,
    )

    instructions = MagicMock()
    instructions.all.return_value = [
        instruction(
            method="fixed",
            fixed_amount=D("3000"),
            percentage=None,
            priority=100,
            deduction_type="other",
            source_reference=None,
        )
    ]

    db.scalars = AsyncMock(return_value=instructions)
    db.flush = AsyncMock()

    with pytest.raises(
        PayrollDeductionResultConflictError,
        match="not authorized",
    ):
        await calculate_payroll_deduction_result(
            db,
            company_id=7,
            payroll_calculation_id=4,
            calculated_by=5,
        )

    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
