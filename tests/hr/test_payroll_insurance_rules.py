from decimal import Decimal as D
import pytest
from pydantic import ValidationError
from app.schemas.payroll_insurance_evidence import InsuranceEvidenceCreate
from app.services.payroll_sick_history_service import service_percentage


@pytest.mark.parametrize('months,days,percent',[(0,0,50),(35,29,50),(36,0,60),(59,29,60),(60,0,70),(95,29,70),(96,0,70),(96,1,100),(120,0,100)])
def test_percentage_tracks_documented_service_thresholds(months,days,percent):
    assert service_percentage(months,days)==percent


@pytest.mark.parametrize('entitlement',['chornobyl_1_3','veteran','rehabilitated'])
def test_documented_full_rate_entitlements(entitlement):
    assert service_percentage(1,0,entitlement)==100


def test_recent_service_cannot_exceed_total_and_entitlement_requires_document():
    base=dict(employee_id=1,case_start='2026-09-28',total_service_months=3,recent_service_months=D(4),
        issued_on='2026-09-28',document_reference='OK-7',request_key='case')
    with pytest.raises(ValidationError): InsuranceEvidenceCreate(**base)
    with pytest.raises(ValidationError): InsuranceEvidenceCreate(**dict(base,recent_service_months=3,entitlement='veteran'))
