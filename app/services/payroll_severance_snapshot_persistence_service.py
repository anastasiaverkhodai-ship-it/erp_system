"""Transaction-owned persistence of immutable severance evidence.

This service does not approve statutory policy, calculate taxes,
authorize disbursement, or commit a transaction.

It must only be called against a database where migration c042
has been applied. The caller owns commit/rollback.
"""

from dataclasses import fields

from sqlalchemy import column, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_severance_statutory_record import (
    PayrollSeveranceStatutoryRecord,
)
from app.services.payroll_severance_statutory_snapshot import (
    SeveranceStatutorySnapshot,
    verify_severance_statutory_snapshot,
)


class SeveranceSnapshotPersistenceError(ValueError):
    """Snapshot is missing, conflicting, or not trustworthy."""


_calculations = table(
    "payroll_calculations",
    column("id"),
    column("company_id"),
)

_SNAPSHOT_FIELDS = tuple(
    item.name for item in fields(SeveranceStatutorySnapshot)
)


def snapshot_to_record_values(
    snapshot: SeveranceStatutorySnapshot,
) -> dict:
    """Produce an ORM payload without modifying the snapshot."""

    verify_severance_statutory_snapshot(snapshot)

    return {
        name: (
            list(getattr(snapshot, name))
            if name == "severance_source_ids"
            else getattr(snapshot, name)
        )
        for name in _SNAPSHOT_FIELDS
    }


def snapshot_from_record(
    record: PayrollSeveranceStatutoryRecord,
) -> SeveranceStatutorySnapshot:
    """Reconstruct and verify all persisted snapshot fields."""

    values = {
        name: (
            tuple(getattr(record, name))
            if name == "severance_source_ids"
            else getattr(record, name)
        )
        for name in _SNAPSHOT_FIELDS
    }

    snapshot = SeveranceStatutorySnapshot(**values)
    verify_severance_statutory_snapshot(snapshot)

    return snapshot


async def persist_severance_statutory_snapshot(
    session: AsyncSession,
    *,
    snapshot: SeveranceStatutorySnapshot,
) -> PayrollSeveranceStatutoryRecord:
    """Insert evidence once; reject any conflicting repeat.

    A row-level lock on the parent calculation serializes concurrent
    writers for a given calculation. PostgreSQL constraints and
    immutable triggers provide additional database protection.

    This function FLUSHES but NEVER COMMITS.
    """

    values = snapshot_to_record_values(snapshot)

    company_id = snapshot.company_id
    calculation_id = snapshot.payroll_calculation_id

    if (
        type(company_id) is not int
        or company_id <= 0
        or type(calculation_id) is not int
        or calculation_id <= 0
    ):
        raise SeveranceSnapshotPersistenceError(
            "Valid company and calculation IDs are required"
        )

    locked_calculation_id = await session.scalar(
        select(_calculations.c.id)
        .where(
            _calculations.c.id == calculation_id,
            _calculations.c.company_id == company_id,
        )
        .with_for_update()
    )

    if locked_calculation_id != calculation_id:
        raise SeveranceSnapshotPersistenceError(
            "Payroll calculation not found in company"
        )

    existing = await session.scalar(
        select(PayrollSeveranceStatutoryRecord).where(
            PayrollSeveranceStatutoryRecord.company_id
            == company_id,
            PayrollSeveranceStatutoryRecord.payroll_calculation_id
            == calculation_id,
        )
    )

    if existing is not None:
        persisted = snapshot_from_record(existing)

        if persisted != snapshot:
            raise SeveranceSnapshotPersistenceError(
                "Historical severance snapshot conflicts with evidence"
            )

        return existing

    record = PayrollSeveranceStatutoryRecord(**values)
    session.add(record)
    await session.flush()

    # Recheck the generated record without persisting anything else.
    if snapshot_from_record(record) != snapshot:
        raise SeveranceSnapshotPersistenceError(
            "Persisted severance snapshot differs from source evidence"
        )

    return record
