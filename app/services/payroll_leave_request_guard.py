"""Compare retries against saved leave calculations without rewriting history."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def number(value, quantum, error_type):
    try:
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError
        return result.quantize(Decimal(quantum), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise error_type('Leave calculation requires finite numeric values') from exc


def source_key(source, error_type):
    def get(name, default=None):
        return source.get(name, default) if isinstance(source, dict) else getattr(source, name, default)
    return (
        str(get('source_type')).strip(), int(get('source_id')),
        get('source_period_start'), get('source_period_end'),
        number(get('earnings_amount', 0), '.01', error_type),
        number(get('eligible_days', 0), '.01', error_type),
        get('source_reference'),
    )


def require_same_leave_request(existing, values, *, requested_sources, saved_sources, error_type):
    if any(getattr(existing, field) != value for field, value in values.items()):
        raise error_type('Existing leave calculation has different request data; use a correction')
    # Omission retains the old read/retry contract. An explicit list asserts
    # the complete provenance, including an explicitly empty list.
    if requested_sources is not None:
        expected = sorted((source_key(s, error_type) for s in requested_sources), key=repr)
        saved = sorted((source_key(s, error_type) for s in saved_sources), key=repr)
        if expected != saved:
            raise error_type('Existing leave calculation has different sources; use a correction')
