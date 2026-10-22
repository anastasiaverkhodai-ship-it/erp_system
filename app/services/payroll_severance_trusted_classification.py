"""Server-owned classification for ordinary Art. 44 termination payments.

Versions preserve historical snapshots; the current version has no invented
legislative expiry. Changes in law require a new reviewed dated version. See docs/phase13_severance_classification.md. Caller policies
and hashes are never authority. Financial consumers must opt in separately.
"""
from datetime import date
from sqlalchemy import select

from app.models.payroll import PayrollCalculation, PayrollInput, PayrollPeriod
from app.models.payroll_severance_statutory_record import PayrollSeveranceStatutoryRecord
from app.services.payroll_severance_tax_classification import SeveranceTaxPolicy
from app.services.payroll_severance_snapshot_integration_service import preview_severance_statutory_snapshot
from app.services.payroll_severance_snapshot_persistence_service import snapshot_from_record
from app.services.payroll_termination_severance_lines import derive_severance_payroll_lines
from app.services.payroll_revision_service import require_current_calculation

TRUSTED_POLICIES = (
    SeveranceTaxPolicy(
        code="UA-ORDINARY-TERMINATION-2026-v1",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 10, 21),
        source_reference="PCU 164.2.20,169.4.1; CMU 1170 I.4; MF 4 income 127; reviewed 2026-10-21",
        personal_income_tax_included=True,
        military_levy_included=True,
        unified_social_contribution_included=False,
        income_code_4df="127",
    ),
    SeveranceTaxPolicy(
        code="UA-ORDINARY-TERMINATION-2026-v2",
        valid_from=date(2026, 10, 22),
        valid_to=None,
        source_reference="PCU 164.2.20,169.4.1; CMU 1170 I.4; MF 4 income 127; reviewed 2026-10-22",
        personal_income_tax_included=True,
        military_levy_included=True,
        unified_social_contribution_included=False,
        income_code_4df="127",
    ),
)


async def trusted_severance_snapshot(db, *, company_id, payroll_calculation_id):
    """Re-derive legal source and server policy; never trust stored labels alone."""
    calculation = await db.scalar(select(PayrollCalculation).where(
        PayrollCalculation.company_id == company_id,
        PayrollCalculation.id == payroll_calculation_id,
    ))
    if calculation is None:
        raise ValueError("Company payroll calculation not found")
    await require_current_calculation(db, calculation=calculation, error_type=ValueError)
    payroll_input = await db.scalar(select(PayrollInput).where(
        PayrollInput.company_id == company_id,
        PayrollInput.id == calculation.payroll_input_id,
    ))
    period = await db.scalar(select(PayrollPeriod).where(
        PayrollPeriod.company_id == company_id,
        PayrollPeriod.id == calculation.payroll_period_id,
    ))
    if payroll_input is None or period is None:
        raise ValueError("Severance payroll context missing")
    derived = await derive_severance_payroll_lines(
        db, company_id=company_id, payroll_input=payroll_input, period=period,
    )
    if len(derived) != 1:
        raise ValueError("One current documented severance source required")
    from app.models.payroll_termination_severance import PayrollTerminationSeverance
    source = await db.scalar(select(PayrollTerminationSeverance).where(
        PayrollTerminationSeverance.company_id == company_id,
        PayrollTerminationSeverance.id == derived[0]["source_termination_severance_id"],
    ))
    snapshot = await preview_severance_statutory_snapshot(
        db, company_id=company_id, payroll_calculation_id=calculation.id,
        income_date=source.termination_date, policies=TRUSTED_POLICIES,
    )
    if (snapshot.severance_source_ids != (source.id,)
            or snapshot.severance_gross != derived[0]["amount"]):
        raise ValueError("Severance calculation differs from current legal source")
    return snapshot


async def require_trusted_severance_snapshot(db, *, company_id, payroll_calculation_id):
    expected = await trusted_severance_snapshot(
        db, company_id=company_id, payroll_calculation_id=payroll_calculation_id,
    )
    record = await db.scalar(select(PayrollSeveranceStatutoryRecord).where(
        PayrollSeveranceStatutoryRecord.company_id == company_id,
        PayrollSeveranceStatutoryRecord.payroll_calculation_id == payroll_calculation_id,
    ))
    if record is None or snapshot_from_record(record) != expected:
        raise ValueError("Severance snapshot does not match trusted dated classification")
    return expected


from app.services.payroll_mutation_guard import serialized_payroll_mutation


@serialized_payroll_mutation(ValueError)
async def persist_trusted_severance_snapshot(db, *, company_id, payroll_calculation_id):
    """Caller owns transaction; there is no caller-supplied policy or date."""
    from app.services.payroll_severance_snapshot_persistence_service import persist_severance_statutory_snapshot
    snapshot = await trusted_severance_snapshot(
        db, company_id=company_id, payroll_calculation_id=payroll_calculation_id,
    )
    return await persist_severance_statutory_snapshot(db, snapshot=snapshot)
