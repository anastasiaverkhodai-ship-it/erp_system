"""Cent-exact allocation within one payment, without financial authorization.

The operational caller must resolve the payment's eligible net base, legal
ceilings, priority and claim amounts from verified persisted documents. This
arithmetic function never treats a technical preview as authority to withhold.
Equal-priority claims share scarce capacity proportionately; ties in residual
cents are resolved by instruction ID, independently of input ordering.
"""
from dataclasses import dataclass
from decimal import Decimal


class PaymentAllocationError(ValueError):
    pass


@dataclass(frozen=True)
class PaymentClaim:
    instruction_id: int
    priority: int
    requested_amount: Decimal
    individual_limit: Decimal


@dataclass(frozen=True)
class PaymentAllocation:
    instruction_id: int
    requested_amount: Decimal
    withheld_amount: Decimal
    unapplied_amount: Decimal


def _cents(value, name):
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise PaymentAllocationError(f'{name} must be finite nonnegative Decimal')
    cents = value * 100
    if cents != cents.to_integral_value():
        raise PaymentAllocationError(f'{name} must have cent precision')
    return int(cents)


def _proportional_capped(claims, budget):
    """Weighted water filling with integer residual cents and per-claim caps."""
    allocated = {key: 0 for key, _, _ in claims}
    active = [(key, requested, min(requested, cap)) for key, requested, cap in claims
              if requested and cap]
    remaining = min(budget, sum(cap for _, _, cap in active))
    while active and remaining:
        total_weight = sum(weight for _, weight, _ in active)
        capped = [(key, weight, cap) for key, weight, cap in active
                  if remaining * weight >= cap * total_weight]
        if capped:
            capped_ids = {key for key, _, _ in capped}
            for key, _, cap in capped:
                allocated[key] = cap
                remaining -= cap
            active = [row for row in active if row[0] not in capped_ids]
            continue
        remainders = []
        for key, weight, cap in active:
            quotient, remainder = divmod(remaining * weight, total_weight)
            allocated[key] = quotient
            remainders.append((-remainder, key))
        residual = remaining - sum(allocated[key] for key, _, _ in active)
        for _, key in sorted(remainders)[:residual]:
            allocated[key] += 1
        remaining = 0
    return allocated


def allocate_payment_deductions(*, eligible_net: Decimal, aggregate_limit: Decimal,
                                claims: tuple[PaymentClaim, ...]) -> tuple[PaymentAllocation, ...]:
    net = _cents(eligible_net, 'eligible net')
    budget = _cents(aggregate_limit, 'aggregate limit')
    if budget > net:
        raise PaymentAllocationError('Aggregate limit exceeds eligible net')
    if type(claims) is not tuple:
        raise PaymentAllocationError('Claims must be a tuple')
    groups = {}
    identifiers = set()
    for claim in claims:
        if type(claim) is not PaymentClaim:
            raise PaymentAllocationError('Invalid payment claim')
        if type(claim.instruction_id) is not int or claim.instruction_id <= 0 or claim.instruction_id in identifiers:
            raise PaymentAllocationError('Invalid or duplicate instruction')
        if type(claim.priority) is not int or claim.priority < 0:
            raise PaymentAllocationError('Invalid priority')
        identifiers.add(claim.instruction_id)
        requested = _cents(claim.requested_amount, 'requested amount')
        cap = _cents(claim.individual_limit, 'individual limit')
        if cap > net:
            raise PaymentAllocationError('Individual limit exceeds eligible net')
        groups.setdefault(claim.priority, []).append((claim.instruction_id, requested, cap))
    result = []
    for priority in sorted(groups):
        group = sorted(groups[priority])
        allocation = _proportional_capped(group, budget)
        budget -= sum(allocation.values())
        for key, requested, _ in group:
            applied = allocation[key]
            result.append(PaymentAllocation(key, Decimal(requested)/100,
                Decimal(applied)/100, Decimal(requested-applied)/100))
    return tuple(result)
