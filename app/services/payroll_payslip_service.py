from __future__ import annotations

from decimal import Decimal

from app.services.payroll_mutation_guard import serialized_payroll_mutation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payroll_deduction_result_service import (
    calculate_payroll_deduction_result,
    require_authorized_deduction_result,
)

from app.models import (
    Employee,
    EmploymentContract,
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollPayslip,
    PayrollPayslipLine,
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
    PayrollDeductionResult,
    PayrollDeductionResultLine,
)


class PayrollPayslipNotFoundError(Exception):
    pass


class PayrollPayslipConflictError(Exception):
    pass


class PayrollPayslipValidationError(Exception):
    pass


async def get_payroll_payslip(
    session: AsyncSession,
    *,
    company_id: int,
    payslip_id: int,
) -> PayrollPayslip:
    payslip = await session.scalar(
        select(PayrollPayslip).where(
            PayrollPayslip.id == payslip_id,
            PayrollPayslip.company_id == company_id,
        )
    )

    if payslip is None:
        raise PayrollPayslipNotFoundError(
            "Payroll payslip not found"
        )

    return payslip


async def get_payroll_payslip_by_calculation(
    session: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> PayrollPayslip | None:
    return await session.scalar(
        select(PayrollPayslip).where(
            PayrollPayslip.company_id == company_id,
            PayrollPayslip.payroll_calculation_id
            == payroll_calculation_id,
        ).order_by(PayrollPayslip.revision.desc()).limit(1)
    )


async def list_payroll_payslip_lines(
    session: AsyncSession,
    *,
    company_id: int,
    payroll_payslip_id: int,
) -> list[PayrollPayslipLine]:
    result = await session.scalars(
        select(PayrollPayslipLine)
        .where(
            PayrollPayslipLine.company_id == company_id,
            PayrollPayslipLine.payroll_payslip_id
            == payroll_payslip_id,
        )
        .order_by(
            PayrollPayslipLine.line_no,
            PayrollPayslipLine.id,
        )
    )

    return list(result.all())


async def get_payslip_generation_sources(
    session: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> tuple[
    PayrollCalculation,
    PayrollStatutoryResult,
    EmploymentContract,
    Employee,
    list[PayrollCalculationLine],
    list[PayrollStatutoryResultLine],
]:
    from app.services.payroll_severance_financial_guard import (
        require_severance_financial_classification,
    )
    await require_severance_financial_classification(
        session,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
        error_type=PayrollPayslipValidationError,
        consumer="payslip",
    )
    calculation = await session.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.id
            == payroll_calculation_id,
            PayrollCalculation.company_id
            == company_id,
        )
    )

    if calculation is None:
        raise PayrollPayslipNotFoundError(
            "Payroll calculation not found"
        )

    statutory_result = await session.scalar(
        select(PayrollStatutoryResult).where(
            PayrollStatutoryResult.company_id
            == company_id,
            PayrollStatutoryResult.payroll_calculation_id
            == calculation.id,
        )
    )

    if statutory_result is None:
        raise PayrollPayslipValidationError(
            "Payroll statutory result is required "
            "before payslip generation"
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
        raise PayrollPayslipNotFoundError(
            "Employment contract not found"
        )

    employee = await session.scalar(
        select(Employee).where(
            Employee.id == contract.employee_id,
            Employee.company_id == company_id,
        )
    )

    if employee is None:
        raise PayrollPayslipNotFoundError(
            "Employee not found"
        )

    calculation_lines = list(
        (
            await session.scalars(
                select(PayrollCalculationLine)
                .where(
                    PayrollCalculationLine.company_id
                    == company_id,
                    PayrollCalculationLine.payroll_calculation_id
                    == calculation.id,
                )
                .order_by(
                    PayrollCalculationLine.line_no,
                    PayrollCalculationLine.id,
                )
            )
        ).all()
    )

    statutory_lines = list(
        (
            await session.scalars(
                select(PayrollStatutoryResultLine)
                .where(
                    PayrollStatutoryResultLine.company_id
                    == company_id,
                    PayrollStatutoryResultLine.payroll_statutory_result_id
                    == statutory_result.id,
                )
                .order_by(
                    PayrollStatutoryResultLine.line_no,
                    PayrollStatutoryResultLine.id,
                )
            )
        ).all()
    )

    return (
        calculation,
        statutory_result,
        contract,
        employee,
        calculation_lines,
        statutory_lines,
    )

@serialized_payroll_mutation(PayrollPayslipValidationError)
async def generate_payroll_payslip(
    session: AsyncSession,
    *,
    company_id: int,
    payroll_calculation_id: int,
    actor_user_id: int,
) -> PayrollPayslip:
    from app.services.payroll_severance_financial_guard import (
        require_severance_financial_classification,
    )
    await require_severance_financial_classification(
        session,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
        error_type=PayrollPayslipValidationError,
        consumer="payslip",
    )
    existing = await get_payroll_payslip_by_calculation(
        session,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )
    if existing is not None:
        from app.services.payroll_deduction_execution_service import withholding_rows
        actual=await withholding_rows(session,company_id,period_id=existing.payroll_period_id,
            contract_id=existing.employment_contract_id)
        saved=await session.scalars(select(PayrollPayslipLine.source_withholding_line_id).where(
            PayrollPayslipLine.company_id==company_id,PayrollPayslipLine.payroll_payslip_id==existing.id,
            PayrollPayslipLine.source_withholding_line_id.is_not(None)))
        if set(saved.all())=={line.id for _,line,_,_,_ in actual if line.amount}:
            return existing

    (
        calculation,
        statutory_result,
        employment_contract,
        employee,
        calculation_lines,
        statutory_lines,
    ) = await get_payslip_generation_sources(
        session,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    calculation_currency = str(
        calculation.currency_code
    ).upper()

    statutory_currency = str(
        statutory_result.currency_code
    ).upper()

    if calculation_currency != statutory_currency:
        raise PayrollPayslipValidationError(
            "payroll calculation and statutory result "
            "currency mismatch"
        )

    if (
        statutory_result.payroll_calculation_id
        != calculation.id
    ):
        raise PayrollPayslipValidationError(
            "statutory result does not belong to "
            "payroll calculation"
        )

    if (
        calculation.employment_contract_id
        != employment_contract.id
    ):
        raise PayrollPayslipValidationError(
            "employment contract does not belong to "
            "payroll calculation"
        )

    if employment_contract.employee_id != employee.id:
        raise PayrollPayslipValidationError(
            "employee does not belong to employment contract"
        )

    gross_amount = Decimal(calculation.gross_amount)
    statutory_gross_amount = Decimal(
        statutory_result.gross_amount
    )
    employee_withholding_amount = Decimal(
        statutory_result.employee_withholding_amount
    )
    employer_contribution_amount = Decimal(
        statutory_result.employer_contribution_amount
    )
    net_amount = Decimal(statutory_result.net_amount)
    non_statutory_deduction_amount = Decimal(0)
    final_payable_amount = net_amount
    deduction_lines = []

    deduction_result = await session.scalar(
        select(PayrollDeductionResult).where(
            PayrollDeductionResult.company_id == company_id,
            PayrollDeductionResult.payroll_calculation_id
            == calculation.id,
        )
    )

    from app.services.payroll_deduction_execution_service import withholding_rows
    operational_rows = await withholding_rows(session, company_id,
        period_id=calculation.payroll_period_id, contract_id=calculation.employment_contract_id)
    operational_history = operational_rows or await withholding_rows(session,company_id,
        period_id=calculation.payroll_period_id,contract_id=calculation.employment_contract_id,active_only=False)
    if deduction_result is None and not operational_history:
        deduction_result = await calculate_payroll_deduction_result(
            session,
            company_id=company_id,
            payroll_calculation_id=calculation.id,
            calculated_by=actor_user_id,
        )

    require_authorized_deduction_result(deduction_result)

    if deduction_result is not None:
        if (
            deduction_result.payroll_statutory_result_id
            != statutory_result.id
        ):
            raise PayrollPayslipValidationError(
                "deduction result does not belong to "
                "payroll statutory result"
            )

        if (
            deduction_result.employment_contract_id
            != calculation.employment_contract_id
        ):
            raise PayrollPayslipValidationError(
                "deduction result employment contract mismatch"
            )

        if (
            str(deduction_result.currency_code).upper()
            != statutory_currency
        ):
            raise PayrollPayslipValidationError(
                "deduction result currency mismatch"
            )

        if (
            Decimal(deduction_result.statutory_net_amount)
            != net_amount
        ):
            raise PayrollPayslipValidationError(
                "deduction result statutory net mismatch"
            )

        non_statutory_deduction_amount = Decimal(
            deduction_result.deduction_amount
        )
        final_payable_amount = Decimal(
            deduction_result.final_payable_amount
        )

        if (
            net_amount - non_statutory_deduction_amount
            != final_payable_amount
        ):
            raise PayrollPayslipValidationError(
                "deduction result final payable does not reconcile"
            )

        deduction_lines = list(
            (
                await session.scalars(
                    select(PayrollDeductionResultLine)
                    .where(
                        PayrollDeductionResultLine.company_id
                        == company_id,
                        PayrollDeductionResultLine
                        .payroll_deduction_result_id
                        == deduction_result.id,
                    )
                    .order_by(
                        PayrollDeductionResultLine.line_no,
                        PayrollDeductionResultLine.id,
                    )
                )
            ).all()
        )

    if operational_rows:
        from types import SimpleNamespace
        operational_total = sum((line.amount for _,line,_,_,_ in operational_rows), Decimal(0))
        non_statutory_deduction_amount = (deduction_result.deduction_amount if deduction_result else Decimal(0)) + operational_total
        final_payable_amount = net_amount - non_statutory_deduction_amount
        deduction_lines = deduction_lines if deduction_result else []
        deduction_lines += [SimpleNamespace(id=None, withholding_line_id=line.id,
            deduction_type='operational_withholding',source_reference=assessment.reference,
            amount=line.amount,currency_code='UAH') for _,line,assessment,_,_ in operational_rows if line.amount]

    if gross_amount != statutory_gross_amount:
        raise PayrollPayslipValidationError(
            "payroll calculation gross amount does not match "
            "statutory result gross amount"
        )

    if (
        gross_amount - employee_withholding_amount
        != net_amount
    ):
        raise PayrollPayslipValidationError(
            "payslip net amount does not reconcile"
        )

    payslip = PayrollPayslip(
        revision=existing.revision+1 if existing else 1,
        company_id=company_id,
        payroll_calculation_id=calculation.id,
        payroll_statutory_result_id=statutory_result.id,
        payroll_period_id=calculation.payroll_period_id,
        employment_contract_id=employment_contract.id,
        employee_id=employee.id,
        employee_number=employee.employee_number,
        employee_first_name=employee.first_name,
        employee_last_name=employee.last_name,
        employee_middle_name=employee.middle_name,
        employee_tax_number=employee.tax_number,
        contract_number=employment_contract.contract_number,
        currency_code=calculation_currency,
        gross_amount=gross_amount,
        employee_withholding_amount=(
            employee_withholding_amount
        ),
        employer_contribution_amount=(
            employer_contribution_amount
        ),
        net_amount=net_amount,
        payroll_deduction_result_id=(
            deduction_result.id
            if deduction_result is not None
            else None
        ),
        non_statutory_deduction_amount=(
            non_statutory_deduction_amount
        ),
        final_payable_amount=final_payable_amount,
        generated_by=actor_user_id,
    )

    session.add(payslip)
    await session.flush()

    line_no = 1

    for source_line in calculation_lines:
        line = PayrollPayslipLine(
            company_id=company_id,
            payroll_payslip_id=payslip.id,
            line_no=line_no,
            line_kind="earning",
            component_code=source_line.line_type,
            description=source_line.description,
            amount=Decimal(source_line.amount),
            currency_code=str(
                source_line.currency_code
            ).upper(),
            source_payroll_calculation_line_id=(
                source_line.id
            ),
            source_payroll_statutory_result_line_id=None,
            source_payroll_deduction_result_line_id=None,
        )
        session.add(line)
        line_no += 1

    employee_withholding_components = {
        "personal_income_tax",
        "military_levy",
    }

    employer_contribution_components = {
        "unified_social_contribution",
    }

    for source_line in statutory_lines:
        component = str(source_line.component)

        if component in employee_withholding_components:
            line_kind = "employee_withholding"
        elif component in employer_contribution_components:
            line_kind = "employer_contribution"
        else:
            raise PayrollPayslipValidationError(
                "unsupported payroll statutory component: "
                f"{component}"
            )

        line = PayrollPayslipLine(
            company_id=company_id,
            payroll_payslip_id=payslip.id,
            line_no=line_no,
            line_kind=line_kind,
            component_code=component,
            description=None,
            amount=Decimal(source_line.amount),
            currency_code=str(
                source_line.currency_code
            ).upper(),
            source_payroll_calculation_line_id=None,
            source_payroll_statutory_result_line_id=(
                source_line.id
            ),
            source_payroll_deduction_result_line_id=None,
        )
        session.add(line)
        line_no += 1

    for source_line in deduction_lines:
        line = PayrollPayslipLine(
            company_id=company_id,
            payroll_payslip_id=payslip.id,
            line_no=line_no,
            line_kind="non_statutory_deduction",
            component_code=source_line.deduction_type,
            description=source_line.source_reference,
            amount=Decimal(source_line.amount),
            currency_code=str(
                source_line.currency_code
            ).upper(),
            source_payroll_calculation_line_id=None,
            source_payroll_statutory_result_line_id=None,
            source_payroll_deduction_result_line_id=source_line.id,
            source_withholding_line_id=getattr(source_line, "withholding_line_id", None),
        )
        session.add(line)
        line_no += 1

    await session.flush()
    await session.refresh(payslip)

    return payslip
