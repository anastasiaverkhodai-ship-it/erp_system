"""C2 company-scoped database approval resolution contracts."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.payroll_deduction_aggregation_policy import (
    DeductionPolicyLine,
)
from app.services.payroll_deduction_aggregation_db_integration import (
    DeductionApprovalResolutionError,
    PersistedDeductionLine,
    preview_deduction_plan_from_db,
)


TODAY = date(2026, 10, 12)


def policy_line(identifier=1):
    return DeductionPolicyLine(
        instruction_id=identifier,
        legal_category="employer_statutory",
        priority=1,
        requested_amount=Decimal("50.00"),
        individual_limit=Decimal("40.00"),
    )


def persisted(approval_id=7):
    return PersistedDeductionLine(
        policy_line=policy_line(),
        approval_id=approval_id,
    )


def make_db(rows):
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db.execute.return_value = result
    return db


async def invoke(db, lines):
    return await preview_deduction_plan_from_db(
        db=db,
        company_id=23,
        effective_on=TODAY,
        available_amount=Decimal("100.00"),
        aggregate_limit=Decimal("30.00"),
        lines=lines,
    )


@pytest.mark.asyncio
async def test_empty_preview_does_not_query_database():
    db = make_db(())

    result = await invoke(db, ())

    assert result.payroll_posting_allowed is False
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_approval_fails_closed():
    db = make_db(())

    with pytest.raises(
        DeductionApprovalResolutionError,
        match="not found",
    ):
        await invoke(db, (persisted(),))


@pytest.mark.asyncio
async def test_duplicate_approval_reference_blocked():
    db = make_db(())

    with pytest.raises(
        DeductionApprovalResolutionError,
        match="reused",
    ):
        await invoke(
            db,
            (persisted(7), persisted(7)),
        )

    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_approval_id_blocked():
    db = make_db(())

    with pytest.raises(
        DeductionApprovalResolutionError,
        match="approval_id",
    ):
        await invoke(db, (persisted(0),))

    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_company_scoped_query_and_contract_delegation():
    row = SimpleNamespace(id=7, company_id=23)
    db = make_db((row,))
    nonposting = SimpleNamespace(
        payroll_posting_allowed=False,
    )

    with patch(
        "app.services.payroll_deduction_aggregation_db_integration"
        "._record_from_model",
        return_value="converted-approved-record",
    ) as convert, patch(
        "app.services.payroll_deduction_aggregation_db_integration"
        ".preview_approved_deduction_plan",
        return_value=nonposting,
    ) as preview:
        result = await invoke(db, (persisted(),))

    assert result is nonposting
    convert.assert_called_once_with(row)
    preview.assert_called_once()

    kwargs = preview.call_args.kwargs

    assert kwargs["company_id"] == 23
    assert kwargs["effective_on"] == TODAY
    assert len(kwargs["lines"]) == 1

    statement = db.execute.await_args.args[0]
    compiled = statement.compile()

    values = tuple(compiled.params.values())

    assert 23 in values
    assert any(
        isinstance(value, (set, list, tuple))
        and 7 in value
        for value in values
    )

    sql = str(statement)
    assert "company_id" in sql
    assert "payroll_deduction_rule_approvals" in sql


@pytest.mark.asyncio
async def test_other_company_approval_not_accepted():
    db = make_db(())

    with pytest.raises(DeductionApprovalResolutionError):
        await invoke(db, (persisted(9),))


@pytest.mark.asyncio
async def test_downstream_posting_authorization_rejected():
    row = SimpleNamespace(id=7, company_id=23)
    db = make_db((row,))

    with patch(
        "app.services.payroll_deduction_aggregation_db_integration"
        "._record_from_model",
        return_value="converted-approved-record",
    ), patch(
        "app.services.payroll_deduction_aggregation_db_integration"
        ".preview_approved_deduction_plan",
        return_value=SimpleNamespace(
            payroll_posting_allowed=True,
        ),
    ):
        with pytest.raises(
            DeductionApprovalResolutionError,
            match="posting",
        ):
            await invoke(db, (persisted(),))


@pytest.mark.asyncio
async def test_invalid_company_rejected_before_query():
    db = make_db(())

    with pytest.raises(DeductionApprovalResolutionError):
        await preview_deduction_plan_from_db(
            db=db,
            company_id=-1,
            effective_on=TODAY,
            available_amount=Decimal("100.00"),
            aggregate_limit=Decimal("30.00"),
            lines=(),
        )

    db.execute.assert_not_awaited()


def test_no_transaction_ownership_or_payroll_posting():
    from pathlib import Path

    source = Path(
        "app/services/payroll_deduction_aggregation_db_integration.py"
    ).read_text()

    assert "await db.commit(" not in source
    assert "await db.rollback(" not in source
    assert "await db.flush(" not in source
    assert "generate_and_post_payroll_deduction_journal(" not in source
