"""D5 readiness must remain non-authorizing."""

from datetime import date

import pytest

from app.services import payroll_d5_source_bound_readiness as service
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def fake_readiness(**changes):
    result = {
        "company_id": 7,
        "payroll_period_id": 14,
        "report_kind": "d5",
        "ready": False,
        "blocking_issues": [
            "DATED_STATUTORY_FORM_MAPPING_MISSING",
        ],
        "calculation_sources": [],
    }
    result.update(changes)
    return result


def fake_bundle(**changes):
    result = {
        "blockers": (
            "D5_NORMATIVE_SOURCE_CHAIN_NOT_APPROVED",
        ),
        "normative_mapping": "NOT_APPROVED",
        "independent_authenticity": "NOT_VERIFIED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
    result.update(changes)
    return result


def install(monkeypatch, *, readiness=None, bundle=None):
    async def read(
        db, *, company_id, payroll_period_id, report_kind
    ):
        assert report_kind == "d5"
        return readiness if readiness is not None else fake_readiness()

    def review(**kwargs):
        return bundle if bundle is not None else fake_bundle()

    monkeypatch.setattr(service, "payroll_regulatory_readiness", read)
    monkeypatch.setattr(
        service, "review_d5_annex_normative_bundle", review
    )


async def run(**kwargs):
    return await service.payroll_d5_source_bound_readiness(
        object(),
        company_id=7,
        payroll_period_id=14,
        source_root="synthetic-only",
        manifest_paths=["manifest.json"],
        candidates=(),
        reporting_date=kwargs.pop(
            "reporting_date", date(2026, 11, 1)
        ),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_payroll_and_normative_blockers_preserved(monkeypatch):
    install(monkeypatch)
    result = await run()

    assert result["ready"] is False
    assert result["official_generation"] == "BLOCKED"
    assert result["official_rows_generated"] == 0
    assert "DATED_STATUTORY_FORM_MAPPING_MISSING" in (
        result["blocking_issues"]
    )
    assert "D5_NORMATIVE_SOURCE_CHAIN_NOT_APPROVED" in (
        result["blocking_issues"]
    )


@pytest.mark.asyncio
async def test_field_mapping_remains_unapproved(monkeypatch):
    install(monkeypatch)
    result = await run()

    assert result["d5_normative_mapping"] == "NOT_APPROVED"
    assert "D5_OFFICIAL_EVENT_FIELD_MAPPING_NOT_APPROVED" in (
        result["blocking_issues"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"company_id": 8},
        {"payroll_period_id": 15},
        {"report_kind": "d1"},
        {"ready": True},
    ],
)
async def test_wrong_payroll_identity_rejected(
    monkeypatch, changes
):
    install(
        monkeypatch,
        readiness=fake_readiness(**changes),
    )

    with pytest.raises(PayrollRegulatoryReportError):
        await run()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"official_generation": "READY"},
        {"official_rows_generated": 1},
        {"normative_mapping": "APPROVED"},
        {"independent_authenticity": "VERIFIED"},
        {"blockers": None},
    ],
)
async def test_forged_bundle_rejected(monkeypatch, changes):
    install(
        monkeypatch,
        bundle=fake_bundle(**changes),
    )

    with pytest.raises(PayrollRegulatoryReportError):
        await run()


@pytest.mark.asyncio
async def test_invalid_reporting_date_rejected(monkeypatch):
    install(monkeypatch)

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="date",
    ):
        await run(reporting_date="2026-11-01")


@pytest.mark.asyncio
async def test_duplicate_blockers_deduplicated(monkeypatch):
    install(
        monkeypatch,
        readiness=fake_readiness(
            blocking_issues=[
                "D5_NORMATIVE_SOURCE_CHAIN_NOT_APPROVED",
                "D5_NORMATIVE_SOURCE_CHAIN_NOT_APPROVED",
            ]
        ),
    )

    result = await run()

    assert result["blocking_issues"].count(
        "D5_NORMATIVE_SOURCE_CHAIN_NOT_APPROVED"
    ) == 1


@pytest.mark.asyncio
async def test_invalid_payroll_blockers_rejected(monkeypatch):
    install(
        monkeypatch,
        readiness=fake_readiness(
            blocking_issues=None,
        ),
    )

    with pytest.raises(PayrollRegulatoryReportError):
        await run()
