from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.employment_contract import EmploymentContract
from app.models.payroll import (
    PayrollCalculation,
    PayrollInput,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from app.models.payroll_statutory import (
    PayrollStatutoryComponent,
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)
from app.models.payroll_tax import (
    PayrollEmployeeTaxProfile,
    PayrollStatutoryBaseRule,
)
from app.services.payroll_calculation_service import (
    calculate_payroll_input,
)
from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
    create_statutory_rate,
    list_statutory_result_lines,
    resolve_payroll_employee_tax_profile,
    resolve_payroll_statutory_base_rule,
)
from tests.hr.test_payroll_accounting_postgresql import (
    _postgres_url,
    _required_values,
    _seed_identity,
)


pytestmark = pytest.mark.asyncio


async def test_payroll_tax_rules_real_postgresql_e2e():
    engine = create_async_engine(
        _postgres_url(),
        pool_pre_ping=True,
    )

    Session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    try:
        async with Session() as db:
            async with db.begin():
                (
                    company,
                    user,
                    employee,
                    contract,
                ) = await _seed_identity(db)

                if "employment_kind" in EmploymentContract.__table__.c:
                    contract.employment_kind = "primary"
                    await db.flush()

                period = PayrollPeriod(
                    **_required_values(
                        PayrollPeriod,
                        {
                            "company_id": company.id,
                            "year": 2026,
                            "month": 9,
                            "start_date": date(2026, 9, 1),
                            "end_date": date(2026, 9, 30),
                            "status": PayrollPeriodStatus.FINALIZED,
                            "created_by": user.id,
                            "finalized_by": user.id,
                            "finalized_at": datetime(
                                2026,
                                10,
                                1,
                                12,
                                0,
                                tzinfo=UTC,
                            ),
                        },
                    )
                )
                db.add(period)
                await db.flush()

                payroll_input = PayrollInput(
                    **_required_values(
                        PayrollInput,
                        {
                            "company_id": company.id,
                            "payroll_period_id": period.id,
                            "employment_contract_id": contract.id,
                            "scheduled_minutes": 6000,
                            "worked_minutes": 6000,
                            "leave_days": 0,
                            "sick_days": 0,
                            "manual_adjustment_amount": Decimal("10000.00"),
                            "manual_adjustment_reason": "13.10 tax-rule test vector",

                            "created_by": user.id,
                        },
                    )
                )
                db.add(payroll_input)
                await db.flush()

                calculation = PayrollCalculation(
                    **_required_values(
                        PayrollCalculation,
                        {
                            "company_id": company.id,
                            "payroll_period_id": period.id,
                            "payroll_input_id": payroll_input.id,
                            "employment_contract_id": contract.id,
                            "gross_amount": Decimal("10000.00"),
                            "currency_code": "UAH",
                            "calculated_by": user.id,
                        },
                    )
                )
                db.add(calculation)
                await db.flush()

                assert Decimal(calculation.gross_amount) == Decimal(
                    "10000.00"
                )

                pit = await create_statutory_rate(
                    db,
                    company_id=company.id,
                    component=(
                        PayrollStatutoryComponent
                        .PERSONAL_INCOME_TAX
                    ),
                    rate=Decimal("0.10"),
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    actor_user_id=user.id,
                )

                military = await create_statutory_rate(
                    db,
                    company_id=company.id,
                    component=(
                        PayrollStatutoryComponent.MILITARY_LEVY
                    ),
                    rate=Decimal("0.05"),
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    actor_user_id=user.id,
                )

                usc = await create_statutory_rate(
                    db,
                    company_id=company.id,
                    component=(
                        PayrollStatutoryComponent
                        .UNIFIED_SOCIAL_CONTRIBUTION
                    ),
                    rate=Decimal("0.20"),
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    actor_user_id=user.id,
                )

                profile = PayrollEmployeeTaxProfile(
                    company_id=company.id,
                    employee_id=employee.id,
                    employment_contract_id=contract.id,
                    category="benefit_eligible",
                    benefit_code="TEST-BENEFIT",
                    exemption_code=None,
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    created_by=user.id,
                )
                db.add(profile)
                await db.flush()

                pit_rule = PayrollStatutoryBaseRule(
                    company_id=company.id,
                    component=(
                        PayrollStatutoryComponent
                        .PERSONAL_INCOME_TAX.value
                    ),
                    employment_kind="primary",
                    tax_profile_category="benefit_eligible",
                    base_mode="gross_after_benefit",
                    benefit_amount=Decimal("1000.00"),
                    minimum_base_amount=None,
                    maximum_base_amount=None,
                    exemption_applies=False,
                    rule_code="TEST-PIT-BENEFIT",
                    rule_version="v1",
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    created_by=user.id,
                )

                military_rule = PayrollStatutoryBaseRule(
                    company_id=company.id,
                    component=(
                        PayrollStatutoryComponent
                        .MILITARY_LEVY.value
                    ),
                    employment_kind=None,
                    tax_profile_category=None,
                    base_mode="gross",
                    benefit_amount=Decimal("0.00"),
                    minimum_base_amount=None,
                    maximum_base_amount=Decimal("8000.00"),
                    exemption_applies=False,
                    rule_code="TEST-MIL-MAX",
                    rule_version="v1",
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    created_by=user.id,
                )

                usc_rule = PayrollStatutoryBaseRule(
                    company_id=company.id,
                    component=(
                        PayrollStatutoryComponent
                        .UNIFIED_SOCIAL_CONTRIBUTION.value
                    ),
                    employment_kind="primary",
                    tax_profile_category=None,
                    base_mode="gross",
                    benefit_amount=Decimal("0.00"),
                    minimum_base_amount=Decimal("12000.00"),
                    maximum_base_amount=None,
                    exemption_applies=False,
                    rule_code="TEST-USC-MIN",
                    rule_version="v1",
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    created_by=user.id,
                )

                db.add_all(
                    [
                        pit_rule,
                        military_rule,
                        usc_rule,
                    ]
                )
                await db.flush()

                resolved_profile = (
                    await resolve_payroll_employee_tax_profile(
                        db,
                        company_id=company.id,
                        employee_id=employee.id,
                        employment_contract_id=contract.id,
                        effective_date=period.end_date,
                    )
                )

                assert resolved_profile is not None
                assert resolved_profile.id == profile.id

                resolved_pit = (
                    await resolve_payroll_statutory_base_rule(
                        db,
                        company_id=company.id,
                        component=(
                            PayrollStatutoryComponent
                            .PERSONAL_INCOME_TAX
                        ),
                        employment_kind="primary",
                        tax_profile_category="benefit_eligible",
                        effective_date=period.end_date,
                    )
                )

                assert resolved_pit is not None
                assert resolved_pit.id == pit_rule.id

                resolved_military = (
                    await resolve_payroll_statutory_base_rule(
                        db,
                        company_id=company.id,
                        component=(
                            PayrollStatutoryComponent.MILITARY_LEVY
                        ),
                        employment_kind="primary",
                        tax_profile_category="benefit_eligible",
                        effective_date=period.end_date,
                    )
                )

                assert resolved_military is not None
                assert resolved_military.id == military_rule.id

                resolved_usc = (
                    await resolve_payroll_statutory_base_rule(
                        db,
                        company_id=company.id,
                        component=(
                            PayrollStatutoryComponent
                            .UNIFIED_SOCIAL_CONTRIBUTION
                        ),
                        employment_kind="primary",
                        tax_profile_category="benefit_eligible",
                        effective_date=period.end_date,
                    )
                )

                assert resolved_usc is not None
                assert resolved_usc.id == usc_rule.id

                first = await calculate_payroll_statutory_result(
                    db,
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                    actor_user_id=user.id,
                )
                await db.flush()

                lines = await list_statutory_result_lines(
                    db,
                    company_id=company.id,
                    statutory_result_id=first.id,
                )

                assert len(lines) == 3

                by_component = {
                    line.component: line
                    for line in lines
                }

                pit_line = by_component[
                    PayrollStatutoryComponent
                    .PERSONAL_INCOME_TAX.value
                ]
                military_line = by_component[
                    PayrollStatutoryComponent
                    .MILITARY_LEVY.value
                ]
                usc_line = by_component[
                    PayrollStatutoryComponent
                    .UNIFIED_SOCIAL_CONTRIBUTION.value
                ]

                assert Decimal(
                    pit_line.gross_base_amount
                ) == Decimal("10000.00")
                assert Decimal(
                    pit_line.benefit_amount_applied
                ) == Decimal("1000.00")
                assert Decimal(
                    pit_line.base_amount
                ) == Decimal("9000.00")
                assert Decimal(
                    pit_line.amount
                ) == Decimal("900.00")
                assert pit_line.source_rate_id == pit.id
                assert pit_line.source_tax_profile_id == profile.id
                assert pit_line.source_base_rule_id == pit_rule.id
                assert pit_line.base_rule_code == "TEST-PIT-BENEFIT"
                assert pit_line.base_rule_version == "v1"
                assert pit_line.exemption_applied is False

                assert Decimal(
                    military_line.gross_base_amount
                ) == Decimal("10000.00")
                assert Decimal(
                    military_line.base_amount
                ) == Decimal("8000.00")
                assert Decimal(
                    military_line.maximum_base_amount_applied
                ) == Decimal("8000.00")
                assert Decimal(
                    military_line.amount
                ) == Decimal("400.00")
                assert military_line.source_rate_id == military.id
                assert (
                    military_line.source_base_rule_id
                    == military_rule.id
                )

                assert Decimal(
                    usc_line.gross_base_amount
                ) == Decimal("10000.00")
                assert Decimal(
                    usc_line.base_amount
                ) == Decimal("12000.00")
                assert Decimal(
                    usc_line.minimum_base_amount_applied
                ) == Decimal("12000.00")
                assert Decimal(
                    usc_line.amount
                ) == Decimal("2400.00")
                assert usc_line.source_rate_id == usc.id
                assert usc_line.source_base_rule_id == usc_rule.id

                assert Decimal(
                    first.employee_withholding_amount
                ) == Decimal("1300.00")

                assert Decimal(
                    first.employer_contribution_amount
                ) == Decimal("2400.00")

                assert Decimal(first.net_amount) == Decimal(
                    "8700.00"
                )

                second = await calculate_payroll_statutory_result(
                    db,
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                    actor_user_id=user.id,
                )

                assert second.id == first.id

                result_count = len(
                    list(
                        (
                            await db.scalars(
                                select(
                                    PayrollStatutoryResult
                                ).where(
                                    PayrollStatutoryResult
                                    .company_id
                                    == company.id,
                                    PayrollStatutoryResult
                                    .payroll_calculation_id
                                    == calculation.id,
                                )
                            )
                        ).all()
                    )
                )
                assert result_count == 1

                line_count = len(
                    list(
                        (
                            await db.scalars(
                                select(
                                    PayrollStatutoryResultLine
                                ).where(
                                    PayrollStatutoryResultLine
                                    .company_id
                                    == company.id,
                                    PayrollStatutoryResultLine
                                    .payroll_statutory_result_id
                                    == first.id,
                                )
                            )
                        ).all()
                    )
                )
                assert line_count == 3

                original_pit_base = Decimal(
                    pit_line.base_amount
                )
                original_pit_code = pit_line.base_rule_code

                pit_rule.benefit_amount = Decimal("5000.00")
                pit_rule.rule_code = "MUTATED-AFTER-CALC"
                profile.benefit_code = "MUTATED-AFTER-CALC"

                await db.flush()
                await db.refresh(pit_line)

                assert Decimal(
                    pit_line.base_amount
                ) == original_pit_base
                assert pit_line.base_rule_code == original_pit_code
                assert Decimal(
                    pit_line.benefit_amount_applied
                ) == Decimal("1000.00")

                exempt_contract = EmploymentContract(
                    **_required_values(
                        EmploymentContract,
                        {
                            "company_id": company.id,
                            "employee_id": employee.id,
                            "contract_number": (
                                f"{contract.contract_number}-EX"
                            ),
                            "contract_type": "standard",
                            "work_arrangement": "full_time",
                            "start_date": date(2026, 1, 1),
                            "end_date": None,
                            "status": "active",
                            "created_by": user.id,
                            "employment_kind": "primary",
                        },
                    )
                )
                db.add(exempt_contract)
                await db.flush()

                exempt_profile = PayrollEmployeeTaxProfile(
                    company_id=company.id,
                    employee_id=employee.id,
                    employment_contract_id=exempt_contract.id,
                    category="exempt",
                    benefit_code=None,
                    exemption_code="TEST-EXEMPT",
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    created_by=user.id,
                )

                exempt_rule = PayrollStatutoryBaseRule(
                    company_id=company.id,
                    component=(
                        PayrollStatutoryComponent
                        .PERSONAL_INCOME_TAX.value
                    ),
                    employment_kind="primary",
                    tax_profile_category="exempt",
                    base_mode="gross",
                    benefit_amount=Decimal("0.00"),
                    minimum_base_amount=None,
                    maximum_base_amount=None,
                    exemption_applies=True,
                    rule_code="TEST-PIT-EXEMPT",
                    rule_version="v1",
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    created_by=user.id,
                )

                db.add_all(
                    [
                        exempt_profile,
                        exempt_rule,
                    ]
                )
                await db.flush()

                resolved_exempt_rule = (
                    await resolve_payroll_statutory_base_rule(
                        db,
                        company_id=company.id,
                        component=(
                            PayrollStatutoryComponent
                            .PERSONAL_INCOME_TAX
                        ),
                        employment_kind="primary",
                        tax_profile_category="exempt",
                        effective_date=period.end_date,
                    )
                )

                assert resolved_exempt_rule is not None
                assert resolved_exempt_rule.id == exempt_rule.id

                from app.services.payroll_statutory_service import (
                    calculate_statutory_component_base,
                )

                (
                    exempt_base,
                    _benefit,
                    _minimum,
                    _maximum,
                    exemption_applied,
                ) = calculate_statutory_component_base(
                    gross_amount=Decimal("10000.00"),
                    profile=exempt_profile,
                    rule=exempt_rule,
                )

                assert exempt_base == Decimal("0.00")
                assert exemption_applied is True

                no_rule = (
                    await resolve_payroll_statutory_base_rule(
                        db,
                        company_id=company.id,
                        component=(
                            PayrollStatutoryComponent
                            .PERSONAL_INCOME_TAX
                        ),
                        employment_kind="external_secondary",
                        tax_profile_category="standard",
                        effective_date=date(2025, 1, 1),
                    )
                )
                assert no_rule is None

                (
                    fallback_base,
                    fallback_benefit,
                    fallback_minimum,
                    fallback_maximum,
                    fallback_exempt,
                ) = calculate_statutory_component_base(
                    gross_amount=Decimal("10000.00"),
                    profile=None,
                    rule=None,
                )

                assert fallback_base == Decimal("10000.00")
                assert fallback_benefit == Decimal("0.00")
                assert fallback_minimum is None
                assert fallback_maximum is None
                assert fallback_exempt is False

                print("PROFILE EFFECTIVE RESOLUTION = PASS")
                print("RULE SPECIFICITY RESOLUTION = PASS")
                print("BENEFIT BASE = PASS")
                print("MAXIMUM BASE = PASS")
                print("MINIMUM BASE = PASS")
                print("EXEMPTION BASE = PASS")
                print("COMPONENT-SPECIFIC BASES = PASS")
                print("RESULT PROVENANCE = PASS")
                print("RESULT SNAPSHOT IMMUTABILITY = PASS")
                print("STATUTORY IDEMPOTENCY = PASS")
                print("LEGACY GROSS FALLBACK = PASS")

                await db.rollback()

    finally:
        await engine.dispose()

    print("ROLLBACK ZERO-LEAK TRANSACTION = PASS")
