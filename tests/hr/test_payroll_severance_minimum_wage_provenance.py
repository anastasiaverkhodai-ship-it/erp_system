import pytest

from app.services.payroll_severance_minimum_wage_provenance import (
    SeveranceMinimumWageProvenanceError,
    VerifiedMinimumWageSource,
    inspect_severance_minimum_wage_source,
)


@pytest.mark.parametrize("company_id,calculation_id", [
    (0, 1),
    (-1, 1),
    (True, 1),
    (1, 0),
    (1, False),
    (1, -1),
])
@pytest.mark.asyncio
async def test_invalid_identifiers_fail_before_db(
    company_id, calculation_id,
):
    with pytest.raises(
        SeveranceMinimumWageProvenanceError,
        match="identifiers",
    ):
        await inspect_severance_minimum_wage_source(
            None,
            company_id=company_id,
            payroll_calculation_id=calculation_id,
        )


def test_verified_source_does_not_grant_financial_permission():
    proof = VerifiedMinimumWageSource(
        company_id=1,
        payroll_calculation_id=2,
        revision=2,
        historical_salary_verified=True,
        minimum_wage_rederived=True,
        complete_payroll_provenance_verified=False,
        financial_execution_allowed=False,
    )

    assert proof.historical_salary_verified
    assert proof.minimum_wage_rederived
    assert not proof.complete_payroll_provenance_verified
    assert not proof.financial_execution_allowed
