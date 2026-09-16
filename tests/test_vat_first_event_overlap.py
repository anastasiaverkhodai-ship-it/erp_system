from datetime import date, timedelta
from decimal import Decimal as D
from itertools import permutations
from types import SimpleNamespace as NS
import pytest
from app.services.tax_recognition_orchestration_service import (
    TaxRecognitionCandidate, TaxRecognitionCandidateKind as K, TaxRecognitionCandidateError,
    build_output_tax_recognition_targets,
)
from app.services.tax_recognition_persistence_service import TaxRecognitionDataIntegrityError
from app.services.input_tax_recognition_evidence_allocation_service import build_input_tax_recognition_evidence_targets
from app.services.tax_recognition_types import TaxRecognitionMethod as M
from app.services.tax_types import TaxDirection, TaxType

D1=date(2026,9,1)


def calc(direction=TaxDirection.OUTPUT, rate=D('.20'), method=M.FIRST_EVENT):
    return NS(id=1,company_id=1,tax_type=TaxType.VAT,direction=direction,recognition_method=method,
        taxable_base=D(100),tax_amount=D(100)*rate,currency_code='UAH')


def candidate(kind, day, base, source=1, rate=D('.20')):
    return TaxRecognitionCandidate(kind,source,D1+timedelta(days=day),D(base),D(base)*rate)


def evidence():
    return NS(id=1,company_id=1,tax_calculation_id=1,evidence_type='registered_tax_invoice',evidence_number='E1',
        evidence_date=D1,credit_available_date=D1,effective_date=D1,evidenced_taxable_base=D(100),
        evidenced_tax_amount=D(20),currency_code='UAH',reversal_of_id=None)


@pytest.mark.parametrize('first', [K.SETTLEMENT,K.FULFILLMENT])
@pytest.mark.parametrize('paid,delivered', [('60','70'),('70','60'),('100','100'),('0','70'),('60','0')])
def test_overlapping_streams_follow_union_not_sum(first,paid,delivered):
    events=(candidate(K.SETTLEMENT,0 if first==K.SETTLEMENT else 1,paid),
            candidate(K.FULFILLMENT,0 if first==K.FULFILLMENT else 1,delivered))
    expected=max(D(paid),D(delivered))
    for order in permutations(events):
        targets=build_output_tax_recognition_targets(calculation=calc(),candidates=order)
        assert sum(t.taxable_base for t in targets)==expected
        assert sum(t.tax_amount for t in targets)==expected*D('.20')


def test_multiple_partials_only_recognize_new_coverage_and_reversal_rebuilds():
    events=(candidate(K.SETTLEMENT,0,'30',1),candidate(K.FULFILLMENT,1,'20',1),
        candidate(K.SETTLEMENT,2,'30',2),candidate(K.FULFILLMENT,3,'50',2),candidate(K.SETTLEMENT,4,'20',3))
    result=build_output_tax_recognition_targets(calculation=calc(),candidates=events)
    assert [(x.event_date,x.tax_amount) for x in result]==[(D1,D(6)),(D1+timedelta(days=2),D(6)),(D1+timedelta(days=3),D(2)),(D1+timedelta(days=4),D(2))]
    # Reversing the last payment leaves net supply 70 and net payments 60.
    assert sum(t.tax_amount for t in build_output_tax_recognition_targets(calculation=calc(),candidates=events[:-1]))==D(14)


def test_input_evidence_segments_do_not_accelerate_credit_date():
    events=(candidate(K.SETTLEMENT,0,'60'),candidate(K.FULFILLMENT,1,'70'),candidate(K.FULFILLMENT,2,'30',2))
    for day,expected in [(0,D(12)),(1,D(14)),(2,D(20))]:
        targets=build_input_tax_recognition_evidence_targets(calculation=calc(TaxDirection.INPUT),
            economic_candidates=events,evidence_events=(evidence(),),as_of_date=D1+timedelta(days=day))
        assert sum(t.tax_amount for t in targets)==expected
        assert all(t.event_date<=D1+timedelta(days=day) for t in targets)
    assert [(t.event_date,t.tax_amount) for t in targets]==[(D1,D(12)),(D1+timedelta(days=1),D(2)),(D1+timedelta(days=2),D(6))]


@pytest.mark.parametrize('rate', [D('.20'),D('.07'),D('.14'),D('0')])
def test_different_rate_pools_are_independent(rate):
    targets=build_output_tax_recognition_targets(calculation=calc(rate=rate),candidates=(candidate(K.SETTLEMENT,0,'60',rate=rate),candidate(K.FULFILLMENT,1,'70',rate=rate)))
    assert sum(t.taxable_base for t in targets)==D(70)
    assert sum(t.tax_amount for t in targets)==D(70)*rate


def test_cash_method_ignores_all_shipments():
    targets=build_output_tax_recognition_targets(calculation=calc(method=M.CASH_METHOD),candidates=(candidate(K.SETTLEMENT,0,'60'),candidate(K.FULFILLMENT,1,'100')))
    assert sum(t.tax_amount for t in targets)==D(12)


@pytest.mark.parametrize('value', ['NaN','Infinity','-Infinity'])
def test_invalid_capacities_rejected(value):
    with pytest.raises(TaxRecognitionCandidateError):
        TaxRecognitionCandidate(K.SETTLEMENT,1,D1,D(value),D(0))


def test_advance_before_rate_start_cannot_use_later_invoice_rate():
    snapshot=calc(rate=D('.14'))
    snapshot.tax_rate_code='VAT14'; snapshot.tax_rate=D('.14'); snapshot.treatment='taxable'
    event=TaxRecognitionCandidate(K.SETTLEMENT,1,date(2021,2,28),D(100),D(14))
    with pytest.raises(TaxRecognitionDataIntegrityError,match='first-event date'):
        build_output_tax_recognition_targets(calculation=snapshot,candidates=(event,))
