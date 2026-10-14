from decimal import Decimal as D
from itertools import permutations
import random
import pytest
from app.services.payroll_deduction_payment_allocator import (
    PaymentClaim as Claim, PaymentAllocationError, allocate_payment_deductions as allocate,
)


def test_equal_priority_is_proportional_and_order_independent():
    claims = (Claim(1, 2, D('100'), D('100')), Claim(2, 2, D('200'), D('200')))
    for ordered in permutations(claims):
        rows = allocate(eligible_net=D('500'), aggregate_limit=D('100'), claims=ordered)
        assert [row.withheld_amount for row in rows] == [D('33.33'), D('66.67')]


def test_individual_cap_reallocates_remaining_capacity():
    rows = allocate(eligible_net=D('500'), aggregate_limit=D('150'), claims=(
        Claim(1, 2, D('200'), D('20')), Claim(2, 2, D('200'), D('200'))))
    assert [row.withheld_amount for row in rows] == [D('20'), D('130')]


def test_later_priority_gets_only_remainder_and_preserves_claim_debt():
    rows = allocate(eligible_net=D('500'), aggregate_limit=D('100'), claims=(
        Claim(2, 5, D('100'), D('100')), Claim(1, 2, D('70'), D('70'))))
    assert [(r.instruction_id, r.withheld_amount, r.unapplied_amount) for r in rows] == [
        (1, D('70'), D('0')), (2, D('30'), D('70'))]


def test_residual_cent_is_deterministic():
    rows = allocate(eligible_net=D('1'), aggregate_limit=D('.01'), claims=(
        Claim(2, 2, D('1'), D('1')), Claim(1, 2, D('1'), D('1'))))
    assert [r.withheld_amount for r in rows] == [D('.01'), D('0')]


@pytest.mark.parametrize('bad', [D('-1'), D('NaN'), D('Infinity'), D('0.001'), 1.0, True])
def test_invalid_money_rejected(bad):
    with pytest.raises(PaymentAllocationError):
        allocate(eligible_net=bad, aggregate_limit=D('0'), claims=())


def test_no_silent_capacity_above_payment():
    with pytest.raises(PaymentAllocationError):
        allocate(eligible_net=D('10'), aggregate_limit=D('11'), claims=())


def test_duplicate_document_cannot_consume_twice():
    with pytest.raises(PaymentAllocationError):
        allocate(eligible_net=D('100'), aggregate_limit=D('50'), claims=(
            Claim(1, 2, D('50'), D('50')), Claim(1, 3, D('50'), D('50'))))


def test_seeded_conservation_caps_and_priority_across_1000_cases():
    rng = random.Random(130029)
    for _ in range(1000):
        claims = tuple(Claim(i+1, rng.randrange(4), D(rng.randrange(10000))/100,
                             D(rng.randrange(10000))/100) for i in range(rng.randrange(1, 15)))
        cap = D(rng.randrange(10000))/100
        rows = allocate(eligible_net=D('100'), aggregate_limit=cap, claims=claims)
        by_id = {c.instruction_id: c for c in claims}
        total = sum((r.withheld_amount for r in rows), D(0))
        assert total <= cap
        for row in rows:
            claim = by_id[row.instruction_id]
            assert 0 <= row.withheld_amount <= min(claim.requested_amount, claim.individual_limit)
            assert row.withheld_amount + row.unapplied_amount == row.requested_amount
            if row.withheld_amount:
                assert all(previous.withheld_amount == min(by_id[previous.instruction_id].requested_amount,
                    by_id[previous.instruction_id].individual_limit) for previous in rows
                    if by_id[previous.instruction_id].priority < claim.priority)
