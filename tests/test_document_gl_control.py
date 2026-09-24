from decimal import Decimal as D
from types import SimpleNamespace as NS
import pytest
from app.services.document_gl_control_service import document_plan


def fixture():
    doc=NS(company_id=1,document_type='receipt',lines=[NS(id=1,quantity=D('3'),price=D('1.005'))])
    rule=NS(company_id=1,document_type='receipt',lines=[
        NS(account_id=1,side='debit',amount_source='line_total'),
        NS(account_id=2,side='credit',amount_source='document_total')])
    return doc,rule


def test_plan_reconstructs_source_rounding_without_gl():
    doc,rule=fixture()
    assert document_plan(doc,rule,{})=={1:[D('3.02'),D(0)],2:[D(0),D('3.02')]}


def test_issue_requires_historical_cost_and_matching_quantity():
    doc,rule=fixture()
    for line in rule.lines:line.amount_source='inventory_cost'
    with pytest.raises(ValueError,match='inventory_cost'):
        document_plan(doc,rule,{})
    costs={1:NS(quantity=D(3),cost_amount=D('2.01'))}
    assert document_plan(doc,rule,costs)[1][0]==D('2.01')
    costs[1].quantity=D(2)
    with pytest.raises(ValueError,match='inventory_cost'):
        document_plan(doc,rule,costs)


def test_foreign_rule_rejected():
    doc,rule=fixture();rule.company_id=2
    with pytest.raises(ValueError,match='incompatible'):
        document_plan(doc,rule,{})
