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


def test_exception_ceiling_does_not_raise_other_claims_joint_limit():
    from app.services.payroll_deduction_payment_allocator import PaymentGroupLimit, allocate_grouped_payment_deductions
    from decimal import Decimal as D
    claims = tuple(Claim(i,0,D(10000),D(200)) for i in (1,2,3)) + (Claim(4,0,D(1),D(700)),)
    result = allocate_grouped_payment_deductions(eligible_net=D(1000),aggregate_limit=D(700),claims=claims,
        group_limits=(PaymentGroupLimit(frozenset({1,2,3}),D(500)),))
    amounts={x.instruction_id:x.withheld_amount for x in result}
    assert sum(amounts[i] for i in (1,2,3)) == D(500)
    assert amounts[4] == D(1)
    assert max(amounts[i] for i in (1,2,3))-min(amounts[i] for i in (1,2,3)) == D('.01')


def test_two_assessments_do_not_double_one_instruction_limit():
    from app.services.payroll_deduction_payment_allocator import PaymentGroupLimit, allocate_grouped_payment_deductions
    from decimal import Decimal as D
    claims=(Claim(1,0,D(300),D(200)),Claim(2,0,D(300),D(200)))
    result=allocate_grouped_payment_deductions(eligible_net=D(1000),aggregate_limit=D(500),claims=claims,
        group_limits=(PaymentGroupLimit(frozenset({1,2}),D(200)),))
    assert [x.withheld_amount for x in result] == [D(100),D(100)]


def test_grouped_allocation_conservation_priority_and_permutation():
    import random
    from app.services.payroll_deduction_payment_allocator import PaymentGroupLimit, allocate_grouped_payment_deductions
    from decimal import Decimal as D
    rng=random.Random(308)
    for _ in range(500):
        claims=tuple(Claim(i,rng.randrange(3),D(rng.randrange(1000))/100,D(5)) for i in range(1,7))
        groups=(PaymentGroupLimit(frozenset({1,2,3,4}),D(5)),PaymentGroupLimit(frozenset({1,2}),D(2)))
        args=dict(eligible_net=D(10),aggregate_limit=D(7),group_limits=groups)
        result=allocate_grouped_payment_deductions(claims=claims,**args)
        assert result==allocate_grouped_payment_deductions(claims=tuple(reversed(claims)),**args)
        amounts={x.instruction_id:x.withheld_amount for x in result}
        assert sum(amounts.values())<=7
        assert sum(amounts[i] for i in (1,2,3,4))<=5
        assert sum(amounts[i] for i in (1,2))<=2
        for claim in claims:
            assert 0<=amounts[claim.instruction_id]<=min(claim.requested_amount,claim.individual_limit)
