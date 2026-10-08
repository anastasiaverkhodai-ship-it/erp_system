from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.services.payroll_mutation_guard import serialized_payroll_mutation

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import PayrollCalculation
from app.models.payroll import PayrollPeriod
from app.models.employment_contract import EmploymentContract
from app.models.payroll_tax import (
    PayrollEmployeeTaxProfile,
    PayrollStatutoryBaseRule,
)
from app.models.payroll_statutory import (
    PayrollStatutoryComponent,
    PayrollStatutoryRate,
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)


MONEY_QUANTUM = Decimal("0.01")


class PayrollStatutoryError(ValueError):
    pass


class PayrollStatutoryNotFoundError(
    PayrollStatutoryError
):
    pass


class PayrollStatutoryConflictError(
    PayrollStatutoryError
):
    pass


class PayrollStatutoryValidationError(
    PayrollStatutoryError
):
    pass


def round_money(value: Decimal) -> Decimal:
    return value.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def validate_rate_window(
    *,
    effective_from: date,
    effective_to: date | None,
) -> None:
    if (
        effective_to is not None
        and effective_to < effective_from
    ):
        raise PayrollStatutoryValidationError(
            "effective_to must be on or after "
            "effective_from"
        )


@serialized_payroll_mutation(PayrollStatutoryValidationError)
async def create_statutory_rate(
    session: AsyncSession,
    *,
    company_id: int,
    component: PayrollStatutoryComponent,
    rate: Decimal,
    effective_from: date,
    effective_to: date | None,
    actor_user_id: int,
    employee_id: int | None = None,
    source_reference: str | None = None,
) -> PayrollStatutoryRate:
    if employee_id is not None:
        from app.models.employee import Employee
        employee = await session.scalar(select(Employee.id).where(
            Employee.company_id == company_id, Employee.id == employee_id))
        if employee is None:
            raise PayrollStatutoryNotFoundError('Employee not found in this company')
        if not source_reference or not source_reference.strip():
            raise PayrollStatutoryValidationError('Individual statutory rate requires supporting evidence')
    validate_rate_window(
        effective_from=effective_from,
        effective_to=effective_to,
    )

    if rate < Decimal("0") or rate > Decimal("1"):
        raise PayrollStatutoryValidationError(
            "statutory rate must be between 0 and 1"
        )

    overlap_stmt = (
        select(PayrollStatutoryRate.id)
        .where(
            PayrollStatutoryRate.company_id
            == company_id,
            PayrollStatutoryRate.employee_id == employee_id,
            PayrollStatutoryRate.component
            == component.value,
            PayrollStatutoryRate.effective_from
            <= (
                effective_to
                if effective_to is not None
                else date.max
            ),
            or_(
                PayrollStatutoryRate.effective_to
                .is_(None),
                PayrollStatutoryRate.effective_to
                >= effective_from,
            ),
        )
        .limit(1)
    )

    existing_id = (
        await session.execute(overlap_stmt)
    ).scalar_one_or_none()

    if existing_id is not None:
        raise PayrollStatutoryConflictError(
            "statutory rate effective window overlaps "
            "an existing rate"
        )

    obj = PayrollStatutoryRate(
        company_id=company_id,
        employee_id=employee_id,
        source_reference=source_reference.strip() if source_reference else None,
        component=component.value,
        rate=rate,
        effective_from=effective_from,
        effective_to=effective_to,
        created_by=actor_user_id,
    )

    session.add(obj)
    await session.flush()
    await session.refresh(obj)

    return obj


async def list_statutory_rates(
    session: AsyncSession,
    *,
    company_id: int,
    component: PayrollStatutoryComponent | None = None,
) -> list[PayrollStatutoryRate]:
    stmt = (
        select(PayrollStatutoryRate)
        .where(
            PayrollStatutoryRate.company_id
            == company_id
        )
        .order_by(
            PayrollStatutoryRate.component.asc(),
            PayrollStatutoryRate.effective_from.asc(),
            PayrollStatutoryRate.id.asc(),
        )
    )

    if component is not None:
        stmt = stmt.where(
            PayrollStatutoryRate.component
            == component.value
        )

    return list(
        (
            await session.execute(stmt)
        ).scalars().all()
    )


async def resolve_statutory_rate(
    session: AsyncSession, *, company_id: int, component: PayrollStatutoryComponent,
    effective_date: date, employee_id: int | None = None,
) -> PayrollStatutoryRate:
    rows = list((await session.scalars(select(PayrollStatutoryRate).where(
        PayrollStatutoryRate.company_id == company_id,
        PayrollStatutoryRate.component == component.value,
        PayrollStatutoryRate.effective_from <= effective_date,
        or_(PayrollStatutoryRate.effective_to.is_(None), PayrollStatutoryRate.effective_to >= effective_date),
        or_(PayrollStatutoryRate.employee_id.is_(None), PayrollStatutoryRate.employee_id == employee_id),
    ))).all())
    preferred = [row for row in rows if employee_id is not None and row.employee_id == employee_id]
    candidates = preferred or [row for row in rows if row.employee_id is None]
    if not candidates:
        raise PayrollStatutoryNotFoundError('no statutory rate applies on ' + effective_date.isoformat())
    if len(candidates) > 1:
        raise PayrollStatutoryValidationError('overlapping statutory rates on ' + effective_date.isoformat())
    return candidates[0]


async def require_uniform_individual_rate_window(session, *, company_id, employee_id,
                                                 date_from, date_to):
    """Do not apply a new individual rate to income earned before its evidence.

    Dated income allocation is required for a window crossing a rate boundary.
    Until that allocation is supplied, rejecting is safer than taxing the entire
    month using its last day's rate.
    """
    rows = (await session.scalars(select(PayrollStatutoryRate).where(
        PayrollStatutoryRate.company_id == company_id,
        PayrollStatutoryRate.employee_id == employee_id,
        PayrollStatutoryRate.effective_from <= date_to,
        or_(PayrollStatutoryRate.effective_to.is_(None), PayrollStatutoryRate.effective_to >= date_from),
    ))).all()
    for row in rows:
        if row.effective_from > date_from or (row.effective_to is not None and row.effective_to < date_to):
            raise PayrollStatutoryValidationError('Individual tax rate changes within the income period; dated income allocation is required')


async def resolve_payroll_employee_tax_profile(
    session: AsyncSession,
    *,
    company_id: int,
    employee_id: int,
    employment_contract_id: int,
    effective_date: date,
) -> PayrollEmployeeTaxProfile | None:
    rows = list(
        (
            await session.scalars(
                select(PayrollEmployeeTaxProfile)
                .where(
                    PayrollEmployeeTaxProfile.company_id == company_id,
                    PayrollEmployeeTaxProfile.employee_id == employee_id,
                    PayrollEmployeeTaxProfile.employment_contract_id
                    == employment_contract_id,
                    PayrollEmployeeTaxProfile.effective_from
                    <= effective_date,
                    or_(
                        PayrollEmployeeTaxProfile.effective_to.is_(None),
                        PayrollEmployeeTaxProfile.effective_to
                        >= effective_date,
                    ),
                )
                .order_by(
                    PayrollEmployeeTaxProfile.effective_from.desc(),
                    PayrollEmployeeTaxProfile.id.desc(),
                )
            )
        ).all()
    )

    if len(rows) > 1:
        raise PayrollStatutoryValidationError(
            "overlapping payroll employee tax profiles"
        )

    return rows[0] if rows else None


async def resolve_payroll_statutory_base_rule(
    session: AsyncSession,
    *,
    company_id: int,
    component: PayrollStatutoryComponent,
    employment_kind: str,
    tax_profile_category: str | None,
    effective_date: date,
) -> PayrollStatutoryBaseRule | None:
    rows = list(
        (
            await session.scalars(
                select(PayrollStatutoryBaseRule)
                .where(
                    PayrollStatutoryBaseRule.company_id == company_id,
                    PayrollStatutoryBaseRule.component
                    == component.value,
                    PayrollStatutoryBaseRule.effective_from
                    <= effective_date,
                    or_(
                        PayrollStatutoryBaseRule.effective_to.is_(None),
                        PayrollStatutoryBaseRule.effective_to
                        >= effective_date,
                    ),
                    or_(
                        PayrollStatutoryBaseRule.employment_kind.is_(None),
                        PayrollStatutoryBaseRule.employment_kind
                        == employment_kind,
                    ),
                    or_(
                        PayrollStatutoryBaseRule.tax_profile_category.is_(None),
                        PayrollStatutoryBaseRule.tax_profile_category
                        == tax_profile_category,
                    ),
                )
            )
        ).all()
    )

    if not rows:
        return None

    def specificity(
        rule: PayrollStatutoryBaseRule,
    ) -> tuple[int, date, int]:
        score = (
            (2 if rule.employment_kind is not None else 0)
            + (
                1
                if rule.tax_profile_category is not None
                else 0
            )
        )
        return score, rule.effective_from, rule.id

    rows.sort(key=specificity, reverse=True)

    best = rows[0]
    best_score = specificity(best)[0]

    same_selector = [
        row
        for row in rows[1:]
        if specificity(row)[0] == best_score
        and row.employment_kind == best.employment_kind
        and row.tax_profile_category
        == best.tax_profile_category
    ]

    if same_selector:
        raise PayrollStatutoryValidationError(
            "overlapping payroll statutory base rules"
        )

    return best


def calculate_statutory_component_base(
    *,
    gross_amount: Decimal,
    profile: PayrollEmployeeTaxProfile | None,
    rule: PayrollStatutoryBaseRule | None,
) -> tuple[
    Decimal,
    Decimal,
    Decimal | None,
    Decimal | None,
    bool,
]:
    gross = round_money(gross_amount)

    if rule is None:
        return (
            gross,
            Decimal("0.00"),
            None,
            None,
            False,
        )

    profile_category = (
        profile.category
        if profile is not None
        else None
    )

    exemption_applied = bool(
        rule.exemption_applies
        and profile_category == "exempt"
    )

    if exemption_applied:
        return (
            Decimal("0.00"),
            Decimal("0.00"),
            (
                round_money(rule.minimum_base_amount)
                if rule.minimum_base_amount is not None
                else None
            ),
            (
                round_money(rule.maximum_base_amount)
                if rule.maximum_base_amount is not None
                else None
            ),
            True,
        )

    benefit_amount = Decimal("0.00")
    base_amount = gross

    if (
        rule.base_mode == "gross_after_benefit"
        and profile_category == "benefit_eligible"
    ):
        benefit_amount = round_money(
            Decimal(rule.benefit_amount)
        )

        base_amount = round_money(
            max(
                Decimal("0.00"),
                gross - benefit_amount,
            )
        )

    minimum_base = (
        round_money(Decimal(rule.minimum_base_amount))
        if rule.minimum_base_amount is not None
        else None
    )

    maximum_base = (
        round_money(Decimal(rule.maximum_base_amount))
        if rule.maximum_base_amount is not None
        else None
    )

    if minimum_base is not None:
        base_amount = max(base_amount, minimum_base)

    if maximum_base is not None:
        base_amount = min(base_amount, maximum_base)

    return (
        round_money(base_amount),
        benefit_amount,
        minimum_base,
        maximum_base,
        False,
    )


async def get_statutory_result(
    session: AsyncSession,
    *,
    company_id: int,
    statutory_result_id: int,
) -> PayrollStatutoryResult:
    stmt = select(
        PayrollStatutoryResult
    ).where(
        PayrollStatutoryResult.company_id
        == company_id,
        PayrollStatutoryResult.id
        == statutory_result_id,
    )

    obj = (
        await session.execute(stmt)
    ).scalar_one_or_none()

    if obj is None:
        raise PayrollStatutoryNotFoundError(
            "payroll statutory result not found"
        )

    return obj


async def get_statutory_result_for_calculation(
    session: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> PayrollStatutoryResult | None:
    stmt = select(
        PayrollStatutoryResult
    ).where(
        PayrollStatutoryResult.company_id
        == company_id,
        PayrollStatutoryResult.payroll_calculation_id
        == payroll_calculation_id,
    )

    return (
        await session.execute(stmt)
    ).scalar_one_or_none()


async def list_statutory_result_lines(
    session: AsyncSession,
    *,
    company_id: int,
    statutory_result_id: int,
) -> list[PayrollStatutoryResultLine]:
    stmt = (
        select(PayrollStatutoryResultLine)
        .where(
            PayrollStatutoryResultLine.company_id
            == company_id,
            PayrollStatutoryResultLine
            .payroll_statutory_result_id
            == statutory_result_id,
        )
        .order_by(
            PayrollStatutoryResultLine.line_no.asc()
        )
    )

    return list(
        (
            await session.execute(stmt)
        ).scalars().all()
    )


async def get_payroll_calculation(
    session: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> PayrollCalculation:
    stmt = select(
        PayrollCalculation
    ).where(
        PayrollCalculation.company_id == company_id,
        PayrollCalculation.id
        == payroll_calculation_id,
    )

    obj = (
        await session.execute(stmt)
    ).scalar_one_or_none()

    if obj is None:
        raise PayrollStatutoryNotFoundError(
            "payroll calculation not found"
        )

    return obj


def calculate_component_amount(
    *,
    base_amount: Decimal,
    rate: Decimal,
) -> Decimal:
    if base_amount < Decimal("0"):
        raise PayrollStatutoryValidationError(
            "statutory base amount cannot be negative"
        )

    if rate < Decimal("0") or rate > Decimal("1"):
        raise PayrollStatutoryValidationError(
            "statutory rate must be between 0 and 1"
        )

    return round_money(base_amount * rate)


@serialized_payroll_mutation(PayrollStatutoryValidationError)
async def calculate_payroll_statutory_result(
    session: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
    actor_user_id: int,
) -> PayrollStatutoryResult:
    existing = await get_statutory_result_for_calculation(
        session,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    if existing is not None:
        return existing

    calculation = await get_payroll_calculation(
        session,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    payroll_period = await session.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.id
            == calculation.payroll_period_id,
            PayrollPeriod.company_id
            == company_id,
        )
    )
    if payroll_period is None:
        raise PayrollStatutoryNotFoundError(
            "Payroll period not found"
        )

    effective_date = payroll_period.end_date

    gross_amount = round_money(
        Decimal(calculation.gross_amount)
    )

    if gross_amount < Decimal("0"):
        raise PayrollStatutoryValidationError(
            "payroll gross amount cannot be negative"
        )

    currency_code = str(
        calculation.currency_code
    ).upper()

    if (
        len(currency_code) != 3
        or not currency_code.isalpha()
    ):
        raise PayrollStatutoryValidationError(
            "payroll calculation currency must be "
            "a 3-letter alphabetic code"
        )

    components = (
        PayrollStatutoryComponent.PERSONAL_INCOME_TAX,
        PayrollStatutoryComponent.MILITARY_LEVY,
        PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION,
    )

    contract = await session.scalar(
        select(EmploymentContract).where(
            EmploymentContract.id
            == calculation.employment_contract_id,
            EmploymentContract.company_id
            == company_id,
        )
    )

    if contract is None:
        raise PayrollStatutoryNotFoundError(
            "Employment contract not found"
        )

    await require_uniform_individual_rate_window(session, company_id=company_id,
        employee_id=contract.employee_id,
        date_from=max(payroll_period.start_date, contract.start_date),
        date_to=min(payroll_period.end_date, contract.end_date or payroll_period.end_date))

    profile = await resolve_payroll_employee_tax_profile(
        session,
        company_id=company_id,
        employee_id=contract.employee_id,
        employment_contract_id=contract.id,
        effective_date=effective_date,
    )

    profile_category = (
        profile.category
        if profile is not None
        else None
    )

    line_payloads = []

    for component in components:
        rate_obj = await resolve_statutory_rate(
            session,
            company_id=company_id,
            component=component,
            effective_date=effective_date,
            employee_id=contract.employee_id,
        )

        base_rule = await resolve_payroll_statutory_base_rule(
            session,
            company_id=company_id,
            component=component,
            employment_kind=contract.employment_kind,
            tax_profile_category=profile_category,
            effective_date=effective_date,
        )

        (
            component_base,
            benefit_amount,
            minimum_base,
            maximum_base,
            exemption_applied,
        ) = calculate_statutory_component_base(
            gross_amount=gross_amount,
            profile=profile,
            rule=base_rule,
        )

        amount = calculate_component_amount(
            base_amount=component_base,
            rate=Decimal(rate_obj.rate),
        )

        line_payloads.append(
            (
                component,
                rate_obj,
                amount,
                component_base,
                benefit_amount,
                minimum_base,
                maximum_base,
                exemption_applied,
                base_rule,
            )
        )

    employee_withholding_amount = sum(
        (
            payload[2]
            for payload in line_payloads
            for component in (payload[0],)
            if component
            in {
                PayrollStatutoryComponent.PERSONAL_INCOME_TAX,
                PayrollStatutoryComponent.MILITARY_LEVY,
            }
        ),
        Decimal("0.00"),
    )

    employer_contribution_amount = sum(
        (
            payload[2]
            for payload in line_payloads
            for component in (payload[0],)
            if component
            == PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION
        ),
        Decimal("0.00"),
    )

    employee_withholding_amount = round_money(
        employee_withholding_amount
    )

    employer_contribution_amount = round_money(
        employer_contribution_amount
    )

    net_amount = round_money(
        gross_amount - employee_withholding_amount
    )

    if net_amount < Decimal("0"):
        raise PayrollStatutoryValidationError(
            "employee statutory withholdings exceed "
            "gross payroll amount"
        )

    result = PayrollStatutoryResult(
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
        currency_code=currency_code,
        gross_amount=gross_amount,
        employee_withholding_amount=(
            employee_withholding_amount
        ),
        employer_contribution_amount=(
            employer_contribution_amount
        ),
        net_amount=net_amount,
        calculated_by=actor_user_id,
    )

    session.add(result)
    await session.flush()

    for line_no, payload in enumerate(
        line_payloads,
        start=1,
    ):
        (
            component,
            rate_obj,
            amount,
            component_base,
            benefit_amount,
            minimum_base,
            maximum_base,
            exemption_applied,
            base_rule,
        ) = payload

        line = PayrollStatutoryResultLine(
            company_id=company_id,
            payroll_statutory_result_id=result.id,
            line_no=line_no,
            component=component.value,
            base_amount=component_base,
            rate=Decimal(rate_obj.rate),
            amount=amount,
            currency_code=currency_code,
            source_rate_id=rate_obj.id,
            source_tax_profile_id=(
                profile.id
                if profile is not None
                else None
            ),
            source_base_rule_id=(
                base_rule.id
                if base_rule is not None
                else None
            ),
            gross_base_amount=gross_amount,
            benefit_amount_applied=benefit_amount,
            minimum_base_amount_applied=minimum_base,
            maximum_base_amount_applied=maximum_base,
            exemption_applied=exemption_applied,
            base_rule_code=(
                base_rule.rule_code
                if base_rule is not None
                else None
            ),
            base_rule_version=(
                base_rule.rule_version
                if base_rule is not None
                else None
            ),
            rate_effective_from=(
                rate_obj.effective_from
            ),
            rate_effective_to=(
                rate_obj.effective_to
            ),
        )

        session.add(line)

    await session.flush()
    await session.refresh(result)

    return result
