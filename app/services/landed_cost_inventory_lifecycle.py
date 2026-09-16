"""Serialize inventory operations and reconcile additional value at their boundary."""
from contextvars import ContextVar
from functools import wraps
from inspect import signature

from app.services.purchase_landed_cost_capitalization_service import lock_landed_cost_company
from app.services.purchase_landed_cost_valuation_service import reconcile_landed_cost_valuation

_operations = ContextVar("landed_cost_inventory_operations", default=())


def landed_cost_inventory_operation(date_argument=None, result_date=None):
    """Nested physical steps share one reconciliation in the caller's transaction.

    Acquire the company lock before document/order/stock locks. The same lock
    serializes capitalization and its reversal with all inventory changes.
    Any error propagates to the caller, which must roll back the transaction.
    """
    def decorate(function):
        parameters = signature(function)

        @wraps(function)
        async def execute(*args, **kwargs):
            bound = parameters.bind(*args, **kwargs)
            bound.apply_defaults()
            db, company_id = bound.arguments["db"], bound.arguments["company_id"]
            key = (id(db), company_id)
            if key in _operations.get():
                return await function(*args, **kwargs)
            await lock_landed_cost_company(db, company_id)
            token = _operations.set((*_operations.get(), key))
            try:
                result = await function(*args, **kwargs)
                operation_date = bound.arguments[date_argument] if date_argument else result_date(result)
                if operation_date is not None:
                    await reconcile_landed_cost_valuation(
                        db, company_id=company_id, adjustment_date=operation_date,
                        created_by=bound.arguments.get("created_by", bound.arguments.get("reversed_by")),
                    )
                return result
            finally:
                _operations.reset(token)
        return execute
    return decorate


def transfer_operation_date(plan):
    if plan.action.value == "noop":
        return None
    return plan.reversal_date or plan.target.transfer_date


def inventory_operation_active(db, company_id):
    """Whether the caller owns the complete inventory/valuation transaction."""
    return (id(db), company_id) in _operations.get()
