from decimal import Decimal as D
import pytest
from app.services.payroll_sick_correction_plan import (
    PayrollFinancialSnapshot as Snapshot, plan_sick_correction, SickCorrectionPlanError,
)


def snapshot(gross):
    gross = D(gross)
    return Snapshot(gross, gross / 2, gross / 2, gross * D('.18'),
                    gross * D('.05'), gross * D('.22'), gross * D('.77'))


@pytest.mark.parametrize('paid', ['0', '300', '770'])
def test_increase_preserves_actual_partial_or_full_payment(paid):
    plan = plan_sick_correction(original=snapshot('1000'), replacement=snapshot('1200'),
                                employee_settled=D(paid), insurer_received=D('500'))
    assert plan.net_delta == D('154')
    assert plan.employee_due == D('924') - D(paid)
    assert plan.employee_overpayment == 0
    assert plan.insurer_funding_due == D('100')
    assert all('cash' not in role and 'bank' not in role for role, _, _ in plan.journal_sides())


def test_decrease_keeps_employee_and_fund_overpayments_separate():
    plan = plan_sick_correction(original=snapshot('1000'), replacement=snapshot('800'),
                                employee_settled=D('770'), insurer_received=D('500'))
    assert plan.employee_due == 0
    assert plan.employee_overpayment == D('154')
    assert plan.insurer_funding_excess == D('100')
    assert plan.net_delta == D('-154')
    assert sum(x[1] for x in plan.journal_sides()) == sum(x[2] for x in plan.journal_sides())


def test_partial_returns_do_not_erase_remaining_overpayment():
    plan = plan_sick_correction(original=snapshot('1000'), replacement=snapshot('800'),
                                employee_settled=D('770'), insurer_received=D('500'),
                                employee_returned=D('50'), insurer_returned=D('25'))
    assert plan.employee_overpayment == D('104')
    assert plan.insurer_funding_excess == D('75')


def test_decrease_before_payment_is_smaller_payable_not_employee_debt():
    plan = plan_sick_correction(original=snapshot('1000'), replacement=snapshot('800'),
                                employee_settled=D('0'), insurer_received=D('0'))
    assert plan.employee_due == D('616')
    assert plan.employee_overpayment == 0


def test_second_correction_uses_previous_revision_not_original_again():
    plan = plan_sick_correction(original=snapshot('800'), replacement=snapshot('900'),
                                employee_settled=D('770'), insurer_received=D('500'))
    assert plan.net_delta == D('77')
    assert plan.employee_overpayment == D('77')


@pytest.mark.parametrize('bad', [D('NaN'), D('Infinity'), D('-1'), D('0.001'), 1.0])
def test_bad_settlement_values_rejected(bad):
    with pytest.raises(SickCorrectionPlanError):
        plan_sick_correction(original=snapshot('1000'), replacement=snapshot('800'),
                             employee_settled=bad, insurer_received=D('0'))


def test_unreconciled_tax_snapshot_rejected():
    with pytest.raises(SickCorrectionPlanError, match='Net'):
        plan_sick_correction(original=snapshot('1000'),
            replacement=Snapshot(D('800'),D('400'),D('400'),D('144'),D('40'),D('176'),D('800')),
            employee_settled=D('0'), insurer_received=D('0'))
