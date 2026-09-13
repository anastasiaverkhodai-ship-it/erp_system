from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.bank_statement_service import (
    append_bank_statement_line,
    create_bank_statement,
    get_bank_statement,
)
from app.services.idempotency_completion_service import (
    complete_idempotent_operation,
)
from app.services.idempotency_decision_types import (
    IdempotencyDecision,
)
from app.services.idempotency_fingerprint_service import (
    generate_request_fingerprint,
)
from app.services.idempotency_reservation_service import (
    reserve_idempotent_request,
)


BANK_STATEMENT_IMPORT_OPERATION = "bank_statement_import"
BANK_STATEMENT_IMPORT_RESULT_TYPE = "bank_statement"


class BankStatementImportError(Exception):
    """Base error for bank statement import orchestration."""


class BankStatementImportInProgressError(BankStatementImportError):
    """Raised when the same import is already being processed."""


class BankStatementImportFailedError(BankStatementImportError):
    """Raised when an existing failed import is not retryable."""


class BankStatementImportResultError(BankStatementImportError):
    """Raised when a stored replay result is invalid."""


@dataclass(frozen=True, slots=True)
class BankStatementImportLine:
    external_line_id: str
    transaction_date: date
    amount: Decimal
    currency_code: str
    value_date: date | None = None
    counterparty_name: str | None = None
    counterparty_account: str | None = None
    payment_reference: str | None = None
    description: str | None = None
    raw_payload: str | None = None


@dataclass(frozen=True, slots=True)
class BankStatementImportRequest:
    bank_account_id: int
    external_id: str
    lines: tuple[BankStatementImportLine, ...]
    statement_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    source_type: str = "manual"
    source_reference: str | None = None
    created_by: int | None = None


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _canonical_payload(
    request: BankStatementImportRequest,
) -> dict[str, Any]:
    return {
        "bank_account_id": request.bank_account_id,
        "external_id": request.external_id.strip(),
        "statement_date": (
            request.statement_date.isoformat()
            if request.statement_date
            else None
        ),
        "period_start": (
            request.period_start.isoformat()
            if request.period_start
            else None
        ),
        "period_end": (
            request.period_end.isoformat()
            if request.period_end
            else None
        ),
        "source_type": request.source_type.strip(),
        "source_reference": _optional_text(
            request.source_reference
        ),
        "created_by": request.created_by,
        "lines": [
            {
                "external_line_id": line.external_line_id.strip(),
                "transaction_date": line.transaction_date.isoformat(),
                "value_date": (
                    line.value_date.isoformat()
                    if line.value_date
                    else None
                ),
                "amount": str(line.amount),
                "currency_code": line.currency_code.strip().upper(),
                "counterparty_name": _optional_text(
                    line.counterparty_name
                ),
                "counterparty_account": _optional_text(
                    line.counterparty_account
                ),
                "payment_reference": _optional_text(
                    line.payment_reference
                ),
                "description": _optional_text(line.description),
                "raw_payload": line.raw_payload,
            }
            for line in request.lines
        ],
    }


async def import_bank_statement(
    *,
    session: AsyncSession,
    company_id: int,
    idempotency_key: str,
    request: BankStatementImportRequest,
):
    """
    Import external bank evidence exactly once.

    This orchestration does not commit or rollback.
    The caller owns the transaction.

    It does not create Payment, JournalEntry or reconciliation.
    """
    request_payload = _canonical_payload(request)
    request_fingerprint = generate_request_fingerprint(
        request_payload
    )

    execution = await reserve_idempotent_request(
        session=session,
        company_id=company_id,
        operation=BANK_STATEMENT_IMPORT_OPERATION,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
    )

    if execution.decision == IdempotencyDecision.REUSE_RESULT:
        result = execution.reusable_result
        if (
            result is None
            or result.result_type
            != BANK_STATEMENT_IMPORT_RESULT_TYPE
            or result.result_id is None
        ):
            raise BankStatementImportResultError(
                "Stored bank statement import result is invalid"
            )

        try:
            statement_id = int(result.result_id)
        except (TypeError, ValueError) as exc:
            raise BankStatementImportResultError(
                "Stored bank statement import result ID is invalid"
            ) from exc

        return await get_bank_statement(
            session,
            company_id=company_id,
            bank_statement_id=statement_id,
        )

    if execution.decision == IdempotencyDecision.ALREADY_IN_PROGRESS:
        raise BankStatementImportInProgressError(
            "Bank statement import is already in progress"
        )

    if execution.decision != IdempotencyDecision.START_NEW:
        raise BankStatementImportFailedError(
            "Bank statement import cannot be started: "
            f"{execution.decision.value}"
        )

    statement = await create_bank_statement(
        session,
        company_id=company_id,
        bank_account_id=request.bank_account_id,
        external_id=request.external_id,
        statement_date=request.statement_date,
        period_start=request.period_start,
        period_end=request.period_end,
        source_type=request.source_type,
        source_reference=request.source_reference,
        created_by=request.created_by,
    )

    for line in request.lines:
        await append_bank_statement_line(
            session,
            company_id=company_id,
            bank_statement_id=statement.id,
            external_line_id=line.external_line_id,
            transaction_date=line.transaction_date,
            value_date=line.value_date,
            amount=line.amount,
            currency_code=line.currency_code,
            counterparty_name=line.counterparty_name,
            counterparty_account=line.counterparty_account,
            payment_reference=line.payment_reference,
            description=line.description,
            raw_payload=line.raw_payload,
        )

    await complete_idempotent_operation(
        session=session,
        company_id=company_id,
        operation=BANK_STATEMENT_IMPORT_OPERATION,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        result_type=BANK_STATEMENT_IMPORT_RESULT_TYPE,
        result_id=str(statement.id),
        result_payload=None,
    )

    return statement
