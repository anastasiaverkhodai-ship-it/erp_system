"""Read-only inventory of legacy physical cash readiness.

Only runs against the disposable PostgreSQL clone.
Presence of an opening review does not itself certify balance.
"""

import os

import pytest
from sqlalchemy import select, func, exists, and_, text

from app.core.database import AsyncSessionLocal
from app.models.cash_desk import CashDesk
from app.models.journal_entry import JournalEntry
from app.models.payment import Payment
from app.models.payroll_disbursement import (
    PayrollDisbursement,
    CashDeskOpeningEvidence,
    CashDeskOpeningReview,
)


@pytest.mark.asyncio
async def test_legacy_cash_enforcement_readiness_inventory():
    assert os.getenv("RUN_POSTGRES_E2E") == "1"

    async with AsyncSessionLocal() as db:
        name = await db.scalar(
            text("SELECT current_database()")
        )
        assert name.startswith("erp_r3_copy_"), (
            "STOP: This audit must run only in disposable PG"
        )

        assert db.bind.dialect.name == "postgresql"

        await db.execute(
            text("SET TRANSACTION READ ONLY")
        )

        desks = (
            await db.scalars(
                select(CashDesk).order_by(
                    CashDesk.company_id,
                    CashDesk.id,
                )
            )
        ).all()

        counters = {
            "all_desks": 0,
            "cash_history": 0,
            "cash_history_without_evidence": 0,
            "cash_history_unreviewed": 0,
            "cash_history_reviewed": 0,
            "desks_without_cash_history": 0,
        }

        for desk in desks:
            counters["all_desks"] += 1

            payment_has_history = await db.scalar(
                select(
                    exists().where(
                        and_(
                            Payment.company_id == desk.company_id,
                            Payment.cash_desk_id == desk.id,
                            JournalEntry.company_id == Payment.company_id,
                            JournalEntry.payment_id == Payment.id,
                            JournalEntry.reversal_of_id.is_(None),
                        )
                    )
                )
            )

            payroll_has_history = await db.scalar(
                select(
                    exists().where(
                        and_(
                            PayrollDisbursement.company_id
                            == desk.company_id,
                            PayrollDisbursement.cash_desk_id == desk.id,
                            JournalEntry.company_id
                            == PayrollDisbursement.company_id,
                            JournalEntry.payroll_disbursement_id
                            == PayrollDisbursement.id,
                            JournalEntry.reversal_of_id.is_(None),
                        )
                    )
                )
            )

            if not (payment_has_history or payroll_has_history):
                counters["desks_without_cash_history"] += 1
                continue

            counters["cash_history"] += 1

            evidence = await db.scalar(
                select(CashDeskOpeningEvidence.id).where(
                    CashDeskOpeningEvidence.company_id
                    == desk.company_id,
                    CashDeskOpeningEvidence.cash_desk_id
                    == desk.id,
                )
            )

            if evidence is None:
                counters["cash_history_without_evidence"] += 1
                continue

            review = await db.scalar(
                select(CashDeskOpeningReview.id).where(
                    CashDeskOpeningReview.company_id
                    == desk.company_id,
                    CashDeskOpeningReview.opening_evidence_id
                    == evidence,
                )
            )

            if review is None:
                counters["cash_history_unreviewed"] += 1
            else:
                counters["cash_history_reviewed"] += 1

        assert counters["all_desks"] == (
            counters["cash_history"]
            + counters["desks_without_cash_history"]
        )

        assert counters["cash_history"] == (
            counters["cash_history_without_evidence"]
            + counters["cash_history_unreviewed"]
            + counters["cash_history_reviewed"]
        )

        print("LEGACY CASH INVENTORY = PASS", flush=True)

        for key, value in counters.items():
            print(
                "LEGACY_" + key.upper() + " = " + str(value),
                flush=True,
            )

        blockers = (
            counters["cash_history_without_evidence"]
            + counters["cash_history_unreviewed"]
        )

        print(
            "LEGACY OPENING PROVENANCE BLOCKERS =",
            blockers,
            flush=True,
        )

        if blockers:
            print(
                "LEGACY FULL ENFORCEMENT = BLOCKED BY OPENING EVIDENCE",
                flush=True,
            )
        else:
            print(
                "LEGACY OPENING PROVENANCE = "
                "NO MISSING OR UNREVIEWED EVIDENCE FOUND",
                flush=True,
            )

        print(
            "LEGACY HISTORICAL GL / CUTOFF CERTIFICATION = PENDING",
            flush=True,
        )
        print(
            "LEGACY AUDIT DATABASE WRITES = 0",
            flush=True,
        )
