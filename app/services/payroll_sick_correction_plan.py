"""Signed financial differences between server-derived payroll snapshots.

This pure module is not an authorization boundary. The writer must load and
verify both snapshots and actual settlements under the company payroll lock.
A correction never reverses a real payment or silently recovers an overpayment.
"""
from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal('0.00')


class SickCorrectionPlanError(ValueError):
    pass


def money(value, label, *, signed=False):
    if not isinstance(value, Decimal) or not value.is_finite():
        raise SickCorrectionPlanError(f'{label} must be finite Decimal money')
    if value != value.quantize(Decimal('.01')) or (not signed and value < 0):
        raise SickCorrectionPlanError(f'Invalid {label}')
    return value


@dataclass(frozen=True)
class PayrollFinancialSnapshot:
    gross: Decimal
    employer_gross: Decimal
    insurer_gross: Decimal
    personal_income_tax: Decimal
    military_levy: Decimal
    employer_contribution: Decimal
    net: Decimal

    def validate(self):
        for name in self.__dataclass_fields__:
            money(getattr(self, name), name)
        if self.employer_gross + self.insurer_gross != self.gross:
            raise SickCorrectionPlanError('Gross funding shares do not reconcile')
        if self.gross - self.personal_income_tax - self.military_levy != self.net:
            raise SickCorrectionPlanError('Net and employee taxes do not reconcile')


@dataclass(frozen=True)
class SickCorrectionFinancialPlan:
    employer_gross_delta: Decimal
    insurer_gross_delta: Decimal
    personal_income_tax_delta: Decimal
    military_levy_delta: Decimal
    employer_contribution_delta: Decimal
    net_delta: Decimal
    employee_balance: Decimal
    employee_due: Decimal
    employee_overpayment: Decimal
    insurer_funding_due: Decimal
    insurer_funding_excess: Decimal

    def journal_sides(self):
        """Role/debit/credit lines; negative changes reverse accrual, not cash."""
        movements = (
            ('payroll_expense', self.employer_gross_delta, True),
            ('insurance_receivable', self.insurer_gross_delta, True),
            ('employer_contribution_expense', self.employer_contribution_delta, True),
            ('payroll_net_payable', self.net_delta, False),
            ('personal_income_tax_payable', self.personal_income_tax_delta, False),
            ('military_levy_payable', self.military_levy_delta, False),
            ('employer_contribution_payable', self.employer_contribution_delta, False),
        )
        result = []
        for role, amount, normal_debit in movements:
            if not amount:
                continue
            debit = abs(amount) if (amount > 0) == normal_debit else ZERO
            credit = ZERO if debit else abs(amount)
            result.append((role, debit, credit))
        if sum(x[1] for x in result) != sum(x[2] for x in result):
            raise SickCorrectionPlanError('Correction journal is unbalanced')
        return tuple(result)


def plan_sick_correction(*, original, replacement, employee_settled,
                         insurer_received, employee_returned=ZERO,
                         insurer_returned=ZERO):
    original.validate()
    replacement.validate()
    for name, value in (
        ('employee_settled', employee_settled), ('insurer_received', insurer_received),
        ('employee_returned', employee_returned), ('insurer_returned', insurer_returned),
    ):
        money(value, name)
    if employee_returned > employee_settled or insurer_returned > insurer_received:
        raise SickCorrectionPlanError('Return exceeds actual settlement')
    balance = replacement.net - employee_settled + employee_returned
    funding = replacement.insurer_gross - insurer_received + insurer_returned
    result = SickCorrectionFinancialPlan(
        employer_gross_delta=replacement.employer_gross - original.employer_gross,
        insurer_gross_delta=replacement.insurer_gross - original.insurer_gross,
        personal_income_tax_delta=replacement.personal_income_tax - original.personal_income_tax,
        military_levy_delta=replacement.military_levy - original.military_levy,
        employer_contribution_delta=replacement.employer_contribution - original.employer_contribution,
        net_delta=replacement.net - original.net,
        employee_balance=balance, employee_due=max(ZERO, balance),
        employee_overpayment=max(ZERO, -balance),
        insurer_funding_due=max(ZERO, funding), insurer_funding_excess=max(ZERO, -funding),
    )
    result.journal_sides()
    return result
