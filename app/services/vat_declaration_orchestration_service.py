from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vat_declaration import VatDeclaration
from app.services.idempotency_completion_service import (
    complete_idempotent_operation,
)
from app.services.idempotency_decision_types import (
    IdempotencyDecision,
)
from app.services.idempotency_fingerprint_service import (
    generate_request_fingerprint,
)
from app.services.idempotency_request_validator import (
    IdempotencyKeyReuseError,
)
from app.services.idempotency_reservation_service import (
    reserve_idempotent_request,
)
from app.services.vat_declaration_persistence_service import (
    build_vat_declaration_snapshot,
)


VAT_DECLARATION_BUILD_OPERATION = "vat_declaration_build"
VAT_DECLARATION_RESULT_TYPE = "vat_declaration"


def _request_key(value: str) -> str:
    key = value.strip()

    if not key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key is required",
        )

    if len(key) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key exceeds 255 characters",
        )

    return key


async def _get_declaration(
    session: AsyncSession,
    *,
    company_id: int,
    declaration_id: int,
) -> VatDeclaration:
    declaration = await session.scalar(
        select(VatDeclaration).where(
            VatDeclaration.company_id == company_id,
            VatDeclaration.id == declaration_id,
        )
    )

    if declaration is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stored VAT declaration idempotency result is invalid",
        )

    return declaration


async def build_vat_declaration_idempotent(
    session: AsyncSession,
    *,
    company_id: int,
    reporting_year: int,
    reporting_month: int,
    source_cutoff_at: datetime,
    created_by: int,
    idempotency_key: str,
) -> VatDeclaration:
    """
    Build an immutable VAT declaration snapshot exactly once per request key.

    This orchestration does not commit or rollback.
    The API caller owns the transaction.
    """

    key = _request_key(idempotency_key)

    request_payload = {
        "reporting_year": reporting_year,
        "reporting_month": reporting_month,
        "source_cutoff_at": source_cutoff_at.isoformat(),
    }

    request_fingerprint = generate_request_fingerprint(
        request_payload
    )

    try:
        execution = await reserve_idempotent_request(
            session=session,
            company_id=company_id,
            operation=VAT_DECLARATION_BUILD_OPERATION,
            idempotency_key=key,
            request_payload=request_payload,
        )
    except IdempotencyKeyReuseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    if execution.decision == IdempotencyDecision.REUSE_RESULT:
        result = execution.reusable_result

        if (
            result is None
            or result.result_type != VAT_DECLARATION_RESULT_TYPE
            or result.result_id is None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Stored VAT declaration idempotency "
                    "result is invalid"
                ),
            )

        try:
            declaration_id = int(result.result_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Stored VAT declaration idempotency "
                    "result id is invalid"
                ),
            ) from exc

        return await _get_declaration(
            session,
            company_id=company_id,
            declaration_id=declaration_id,
        )

    if execution.decision == IdempotencyDecision.ALREADY_IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VAT declaration build is already in progress",
        )

    if execution.decision != IdempotencyDecision.START_NEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "VAT declaration build cannot start: "
                f"{execution.decision.value}"
            ),
        )

    declaration = await build_vat_declaration_snapshot(
        db=session,
        company_id=company_id,
        reporting_year=reporting_year,
        reporting_month=reporting_month,
        source_cutoff_at=source_cutoff_at,
        created_by=created_by,
    )

    await complete_idempotent_operation(
        session=session,
        company_id=company_id,
        operation=VAT_DECLARATION_BUILD_OPERATION,
        idempotency_key=key,
        request_fingerprint=request_fingerprint,
        result_type=VAT_DECLARATION_RESULT_TYPE,
        result_id=str(declaration.id),
        result_payload=None,
    )

    return declaration
