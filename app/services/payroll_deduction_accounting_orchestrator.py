"""D3-B2-R2: trusted-read, non-posting accounting orchestration.

D1 and D2 are executed using the supplied database session.

This is NOT legal approval of caller-supplied priorities or global caps.
It does not authorize payroll deductions or accounting journal posting.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payroll_deduction_approval_preflight_service import (
    verify_deduction_approval_preflight,
)
from app.services.payroll_deduction_batch_limit_preview import (
    BatchLimitRequest,
    BatchLimitPreview,
    preview_payroll_deduction_batch_limits,
)
from app.services.payroll_deduction_accounting_preflight import (
    AccountingPreflightResult,
    evaluate_accounting_preflight,
)


class AccountingOrchestrationError(ValueError):
    """Reject inconsistent or unsafe orchestration inputs/results."""


@dataclass(frozen=True)
class AccountingOrchestrationRequest:
    instruction_id: int
    legal_category: str
    rule_version: str
    priority: int
    requested_amount: Decimal


@dataclass(frozen=True)
class AccountingOrchestrationResult:
    company_id: int
    effective_on: date
    accounting_preflights: tuple[AccountingPreflightResult, ...]
    batch_preview: BatchLimitPreview
    financial_execution_authorized: bool = False
    journal_posting_allowed: bool = False
    legal_authorization: str = "NOT_AUTHORIZED"


def _money(value, field):
    if type(value) is not Decimal or not value.is_finite():
        raise AccountingOrchestrationError(
            f"{field} must be a finite Decimal"
        )

    if value < 0:
        raise AccountingOrchestrationError(
            f"{field} must be nonnegative"
        )

    if value * 100 != (value * 100).to_integral_value():
        raise AccountingOrchestrationError(
            f"{field} exceeds cent precision"
        )

    return value


def _validate_requests(
    requests: tuple[AccountingOrchestrationRequest, ...],
) -> None:
    if type(requests) is not tuple or not requests:
        raise AccountingOrchestrationError(
            "A nonempty tuple of requests is required"
        )

    instruction_ids = set()
    priorities = set()

    for request in requests:
        if type(request) is not AccountingOrchestrationRequest:
            raise AccountingOrchestrationError(
                "Invalid orchestration request"
            )

        if type(request.instruction_id) is not int or request.instruction_id <= 0:
            raise AccountingOrchestrationError(
                "Invalid instruction ID"
            )

        if (
            type(request.legal_category) is not str
            or not request.legal_category.strip()
        ):
            raise AccountingOrchestrationError(
                "Legal category is required"
            )

        if (
            type(request.rule_version) is not str
            or not request.rule_version.strip()
        ):
            raise AccountingOrchestrationError(
                "Rule version is required"
            )

        if type(request.priority) is not int or request.priority < 0:
            raise AccountingOrchestrationError(
                "Invalid technical priority"
            )

        _money(request.requested_amount, "requested_amount")

        if request.instruction_id in instruction_ids:
            raise AccountingOrchestrationError(
                "Duplicate instruction ID"
            )

        if request.priority in priorities:
            raise AccountingOrchestrationError(
                "Ambiguous technical priority"
            )

        instruction_ids.add(request.instruction_id)
        priorities.add(request.priority)


async def evaluate_trusted_accounting_orchestration(
    db: AsyncSession,
    *,
    company_id: int,
    effective_on: date,
    statutory_net: Decimal,
    global_aggregate_cap: Decimal,
    requests: tuple[AccountingOrchestrationRequest, ...],
) -> AccountingOrchestrationResult:
    """Resolve D1/D2 through DB-backed services, then evaluate D3.

    All priorities and the global cap remain caller-supplied technical
    assumptions, not established legal facts. No journal is posted.
    """

    if type(company_id) is not int or company_id <= 0:
        raise AccountingOrchestrationError(
            "Invalid company ID"
        )

    if type(effective_on) is not date:
        raise AccountingOrchestrationError(
            "Invalid effective date"
        )

    net = _money(statutory_net, "statutory_net")
    cap = _money(global_aggregate_cap, "global_aggregate_cap")

    if cap > net:
        raise AccountingOrchestrationError(
            "Aggregate cap exceeds statutory net"
        )

    _validate_requests(requests)

    # Complete local validation before the first database read.
    ordered = tuple(sorted(
        requests,
        key=lambda request: request.priority,
    ))

    resolved = {}

    for request in ordered:
        d1 = await verify_deduction_approval_preflight(
            db,
            company_id=company_id,
            instruction_id=request.instruction_id,
            legal_category=request.legal_category,
            rule_version=request.rule_version,
            effective_on=effective_on,
        )

        # Check the full identity of the verified D1 result.
        if (
            getattr(d1, "company_id", None) != company_id
            or getattr(d1, "instruction_id", None)
            != request.instruction_id
            or getattr(d1, "legal_category", None)
            != request.legal_category
            or getattr(d1, "rule_version", None)
            != request.rule_version
            or getattr(d1, "effective_on", None)
            != effective_on
            or getattr(
                d1,
                "financial_execution_authorized",
                None,
            ) is not False
        ):
            raise AccountingOrchestrationError(
                "D1 trusted preflight result mismatch"
            )

        resolved[request.instruction_id] = d1

    d2_requests = tuple(
        BatchLimitRequest(
            instruction_id=request.instruction_id,
            priority=request.priority,
            requested_amount=request.requested_amount,
        )
        for request in ordered
    )

    batch = await preview_payroll_deduction_batch_limits(
        db,
        company_id=company_id,
        statutory_net=net,
        global_aggregate_cap=cap,
        requests=d2_requests,
    )

    if type(batch) is not BatchLimitPreview:
        raise AccountingOrchestrationError(
            "Invalid D2 batch result type"
        )

    if (
        batch.financial_execution_authorized is not False
        or batch.legal_authorization != "NOT_AUTHORIZED"
    ):
        raise AccountingOrchestrationError(
            "D2 must remain non-authorizing"
        )

    expected_ids = {
        request.instruction_id for request in ordered
    }

    actual_ids = {
        getattr(line, "instruction_id", None)
        for line in batch.lines
    }

    if len(batch.lines) != len(ordered) or actual_ids != expected_ids:
        raise AccountingOrchestrationError(
            "D2 batch instruction identity mismatch"
        )

    preflights = []

    for request in ordered:
        result = evaluate_accounting_preflight(
            company_id=company_id,
            instruction_id=request.instruction_id,
            requested_amount=request.requested_amount,
            d1_preflight=resolved[request.instruction_id],
            d2_preview=batch,
        )

        if (
            type(result) is not AccountingPreflightResult
            or result.company_id != company_id
            or result.instruction_id != request.instruction_id
            or result.financial_execution_authorized is not False
            or result.journal_posting_allowed is not False
            or result.legal_authorization != "NOT_AUTHORIZED"
        ):
            raise AccountingOrchestrationError(
                "D3 accounting preflight rejected"
            )

        preflights.append(result)

    return AccountingOrchestrationResult(
        company_id=company_id,
        effective_on=effective_on,
        accounting_preflights=tuple(preflights),
        batch_preview=batch,
    )
