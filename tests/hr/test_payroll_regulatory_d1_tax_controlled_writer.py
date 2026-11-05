"""D1 statutory gate: source consistency and denial paths."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.payroll_regulatory_d1_tax_controlled_writer import (
    D1StatutorySourceControlError,
    money,
    validate_d1_source_control,
    persist_d1_tax_controlled_internal_review,
)


def sample_review():
    return {
        "company_id": 10,
        "payroll_period_id": 20,
        "report_kind": "d1",
        "review_only": True,
        "official_statutory_generation": "BLOCKED",
        "approved_normative_mapping": False,
        "source_count": 1,
        "rows": [{"internal_source": 1}],
        "gross_total": "100.00",
        "employee_withholding_total": "20.00",
        "employer_contribution_total": "22.00",
    }


def test_verified_aggregate_control():
    control = validate_d1_source_control(
        sample_review(),
        company_id=10,
        payroll_period_id=20,
        independent_gross="100.00",
    )

    assert control["gross_total"] == "100.00"
    assert control["employee_withholding_total"] == "20.00"
    assert control["employer_contribution_total"] == "22.00"
    assert control["separate_pit_verified"] is False
    assert control["official_generation"] == "BLOCKED"


@pytest.mark.parametrize(
    "change",
    [
        {"company_id": 11},
        {"payroll_period_id": 21},
        {"report_kind": "4df"},
        {"review_only": False},
        {"official_statutory_generation": "ALLOWED"},
        {"approved_normative_mapping": True},
        {"source_count": 2},
        {"source_count": 0},
        {"rows": []},
        {"gross_total": "101.00"},
        {"employee_withholding_total": "-1.00"},
        {"employer_contribution_total": "NaN"},
    ],
)
def test_invalid_control_rejected(change):
    review = sample_review()
    review.update(change)

    with pytest.raises(D1StatutorySourceControlError):
        validate_d1_source_control(
            review,
            company_id=10,
            payroll_period_id=20,
            independent_gross="100.00",
        )


@pytest.mark.parametrize(
    "amount",
    [None, True, 1.1, "NaN", "Infinity", "-1", "1.001"],
)
def test_unsafe_money(amount):
    with pytest.raises(D1StatutorySourceControlError):
        money(amount)


@pytest.mark.asyncio
async def test_strict_entrypoint_persists_only_after_d1_control():
    refs = {"D1": 1, "4DF": 2, "D5": 3, "D6": 4}

    gross = AsyncMock(
        return_value={
            "D1": {"gross": "100.00"},
            "4DF": {"gross": "100.00"},
            "D5": {},
            "D6": {},
        }
    )

    statutory = AsyncMock(return_value=sample_review())
    writer = AsyncMock(
        return_value={
            "id": 5,
            "version": 1,
            "created": True,
            "independent_gross_verified": True,
            "official_generation": "BLOCKED",
        }
    )

    module = (
        "app.services."
        "payroll_regulatory_d1_tax_controlled_writer"
    )

    with (
        patch(
            f"{module}.derive_independent_gross_controls",
            gross,
        ),
        patch(
            f"{module}.build_verified_d1_review",
            statutory,
        ),
        patch(
            f"{module}.persist_gross_verified_internal_four_form_review",
            writer,
        ),
    ):
        result = await persist_d1_tax_controlled_internal_review(
            object(),
            company_id=10,
            payroll_period_id=20,
            payroll_period="2026-10",
            report_references=refs,
            request_key="verified-d1-test",
        )

    assert result["d1_statutory_review_verified"] is True
    assert result["d1_control_persisted_in_snapshot"] is False
    assert result["official_generation"] == "BLOCKED"
    writer.assert_awaited_once()


@pytest.mark.asyncio
async def test_bad_d1_source_cannot_reach_writer():
    bad = sample_review()
    bad["gross_total"] = "999.00"

    module = (
        "app.services."
        "payroll_regulatory_d1_tax_controlled_writer"
    )

    with (
        patch(
            f"{module}.derive_independent_gross_controls",
            AsyncMock(return_value={
                "D1": {"gross": "100.00"},
            }),
        ),
        patch(
            f"{module}.build_verified_d1_review",
            AsyncMock(return_value=bad),
        ),
        patch(
            f"{module}.persist_gross_verified_internal_four_form_review",
            AsyncMock(),
        ) as writer,
    ):
        with pytest.raises(D1StatutorySourceControlError):
            await persist_d1_tax_controlled_internal_review(
                object(),
                company_id=10,
                payroll_period_id=20,
                payroll_period="2026-10",
                report_references={
                    "D1": 1, "4DF": 2,
                    "D5": 3, "D6": 4,
                },
                request_key="bad-d1-test",
            )

        writer.assert_not_awaited()
