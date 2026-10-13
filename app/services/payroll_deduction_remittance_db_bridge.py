"""D4-B2: database-backed, non-executing remittance integration.

Uses existing D3, remainder and remittance services.

The original amount is the nominal fixed instruction amount,
NOT evidence of actual payroll withholding.

No payments, journal postings, inserts, updates, commits or rollbacks.
Caller owns the active PostgreSQL transaction.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_deduction import (
    PayrollDeductionInstruction,
)

from app.services.payroll_deduction_accounting_orchestrator import (
    AccountingOrchestrationRequest,
    evaluate_trusted_accounting_orchestration,
)

from app.services.payroll_deduction_remainder_persistence_service import (
    preview_persisted_remainder,
)

from app.services.payroll_deduction_remittance_persistence_service import (
    read_persisted_remittance_balance,
)

from app.services.payroll_deduction_remittance_reconciliation import (
    InstructionEvidence,
    RemainderEvidence,
    RemittanceEvidence,
    reconcile_remittance_sources,
)

from app.services.payroll_deduction_remittance_readiness import (
    evaluate_remittance_readiness,
)


class RemittanceDatabaseBridgeError(ValueError):
    """Reject an unsafe or unsupported remittance preview."""


def _id(value, name):
    if type(value) is not int or value <= 0:
        raise RemittanceDatabaseBridgeError(
            f"Invalid {name}"
        )
    return value


def _money(value, name):
    if type(value) is not Decimal:
        raise RemittanceDatabaseBridgeError(
            f"{name} must be Decimal"
        )

    if (
        not value.is_finite()
        or value < 0
        or value * 100 != (value * 100).to_integral_value()
    ):
        raise RemittanceDatabaseBridgeError(
            f"Invalid {name}"
        )

    return value


async def evaluate_database_backed_remittance_readiness(
    db: AsyncSession,
    *,
    company_id: int,
    instruction_id: int,
    legal_category: str,
    rule_version: str,
    effective_on: date,
    priority: int,
    requested_amount: Decimal,
    statutory_net: Decimal,
    global_aggregate_cap: Decimal,
):
    """Read trusted technical sources without permitting execution.

    Scope: fixed-amount deduction instructions only.

    A configured fixed amount is NOT a verified withheld amount.
    This function cannot authorize remittance or payment.
    """

    if not isinstance(db, AsyncSession):
        raise RemittanceDatabaseBridgeError(
            "AsyncSession required"
        )

    if not db.in_transaction():
        raise RemittanceDatabaseBridgeError(
            "Caller-owned transaction required"
        )

    _id(company_id, "company_id")
    _id(instruction_id, "instruction_id")

    if type(priority) is not int or priority < 0:
        raise RemittanceDatabaseBridgeError(
            "Invalid priority"
        )

    if type(effective_on) is not date:
        raise RemittanceDatabaseBridgeError(
            "Invalid effective date"
        )

    if type(legal_category) is not str or not legal_category.strip():
        raise RemittanceDatabaseBridgeError(
            "Invalid legal category"
        )

    if type(rule_version) is not str or not rule_version.strip():
        raise RemittanceDatabaseBridgeError(
            "Invalid rule version"
        )

    requested = _money(
        requested_amount,
        "requested_amount",
    )
    net = _money(statutory_net, "statutory_net")
    cap = _money(
        global_aggregate_cap,
        "global_aggregate_cap",
    )

    if cap > net:
        raise RemittanceDatabaseBridgeError(
            "Global cap exceeds statutory net"
        )

    # Source 1: actual company-owned instruction.
    instruction = await db.scalar(
        select(PayrollDeductionInstruction).where(
            PayrollDeductionInstruction.company_id == company_id,
            PayrollDeductionInstruction.id == instruction_id,
        )
    )

    if instruction is None:
        raise RemittanceDatabaseBridgeError(
            "Instruction not found in company"
        )

    if instruction.method != "fixed":
        raise RemittanceDatabaseBridgeError(
            "Only fixed-amount instruction preview supported"
        )

    original = _money(
        instruction.fixed_amount,
        "instruction.fixed_amount",
    )

    currency = instruction.currency_code

    if (
        type(currency) is not str
        or len(currency) != 3
        or currency != currency.upper()
        or not currency.isascii()
        or not currency.isalpha()
    ):
        raise RemittanceDatabaseBridgeError(
            "Invalid instruction currency"
        )

    # D3 independently queries persisted verified evidence
    # and approved rule through D1/D2.
    accounting = await evaluate_trusted_accounting_orchestration(
        db,
        company_id=company_id,
        effective_on=effective_on,
        statutory_net=net,
        global_aggregate_cap=cap,
        requests=(
            AccountingOrchestrationRequest(
                instruction_id=instruction_id,
                legal_category=legal_category,
                rule_version=rule_version,
                priority=priority,
                requested_amount=requested,
            ),
        ),
    )

    if (
        accounting.financial_execution_authorized is not False
        or accounting.journal_posting_allowed is not False
        or accounting.legal_authorization != "NOT_AUTHORIZED"
    ):
        raise RemittanceDatabaseBridgeError(
            "Accounting must remain non-authorizing"
        )

    # Sources 2 and 3: read the existing PostgreSQL ledgers.
    # Both services require the same caller-owned transaction.
    remainder = await preview_persisted_remainder(
        db=db,
        company_id=company_id,
        instruction_id=instruction_id,
    )

    remittance = await read_persisted_remittance_balance(
        db=db,
        company_id=company_id,
        instruction_id=instruction_id,
    )

    instruction_evidence = InstructionEvidence(
        company_id=company_id,
        instruction_id=instruction_id,
        original_amount=original,
        currency_code=currency,
    )

    remainder_evidence = RemainderEvidence(
        company_id=remainder.company_id,
        instruction_id=remainder.instruction_id,
        original_amount=remainder.original_amount,
        currency_code=remainder.currency_code,
        applied_amount=remainder.applied_amount,
        remaining_amount=remainder.remaining_amount,
        event_count=remainder.event_count,
    )

    remittance_evidence = RemittanceEvidence(
        company_id=remittance.company_id,
        instruction_id=remittance.instruction_id,
        beneficiary_reference=remittance.beneficiary_reference,
        currency_code=remittance.currency_code,
        available_amount=remittance.available_amount,
        allocated_amount=remittance.allocated_amount,
        remaining_amount=remittance.remaining_amount,
        event_count=remittance.event_count,
    )

    reconciliation = reconcile_remittance_sources(
        instruction=instruction_evidence,
        remainder=remainder_evidence,
        remittance=remittance_evidence,
    )

    # D4-B1 checks the identity and non-execution boundary.
    result = evaluate_remittance_readiness(
        company_id=company_id,
        instruction_id=instruction_id,
        accounting_orchestration=accounting,
        remittance_reconciliation=reconciliation,
    )

    if (
        result.financial_execution_authorized is not False
        or result.journal_posting_allowed is not False
        or result.payment_execution_allowed is not False
        or result.withheld_funds_verified is not False
        or result.bank_payment_verified is not False
        or result.legal_authorization != "NOT_AUTHORIZED"
    ):
        raise RemittanceDatabaseBridgeError(
            "Unsafe remittance readiness result"
        )

    return result
