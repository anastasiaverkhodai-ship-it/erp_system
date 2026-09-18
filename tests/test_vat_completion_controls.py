from datetime import date
from decimal import Decimal
import pytest
from pydantic import ValidationError
from app.schemas.tax_invoice_correction import OutputTaxInvoiceCorrectionLineCreate
from app.services.tax_invoice_correction_source_resolver_service import normalize_metadata_replacement,TaxInvoiceCorrectionSourceError
from app.services.tax_invoice_correction_accounting_service import correction_posting_date,registration_party,CorrectionAccountingError


@pytest.mark.parametrize('vat,amount,expected',[('111',-120,'buyer'),('111',120,'seller'),('111',0,'seller'),(None,-120,'seller')])
def test_registration_party_is_based_on_compensation_and_buyer(vat,amount,expected):
    assert registration_party(buyer_vat_number=vat,compensation_delta=Decimal(amount))==expected


def test_output_increase_is_not_delayed_until_receipt():
    assert correction_posting_date(document_date=date(2026,9,1),compensation_delta=120,party='seller')==date(2026,9,1)


def test_decrease_requires_registration_and_uses_timely_or_late_period():
    args=dict(document_date=date(2026,8,31),compensation_delta=-120,party='buyer')
    assert correction_posting_date(**args) is None
    assert correction_posting_date(**args,received_on=date(2026,8,31),registered_on=date(2026,9,18))==date(2026,8,31)
    assert correction_posting_date(**args,received_on=date(2026,8,31),registered_on=date(2026,9,19))==date(2026,9,19)
    with pytest.raises(CorrectionAccountingError):
        correction_posting_date(**args,registered_on=date(2026,9,1))


def test_zero_amount_requisite_correction_is_explicit():
    line=OutputTaxInvoiceCorrectionLineCreate(line_number=1,source_kind='metadata_correction',source_id=4,reason_code='requisite',replacement={'description':'Correct description'})
    assert line.replacement.description=='Correct description'
    with pytest.raises(ValidationError):
        OutputTaxInvoiceCorrectionLineCreate(line_number=1,source_kind='metadata_correction',source_id=4,reason_code='requisite')
    with pytest.raises(ValidationError):
        OutputTaxInvoiceCorrectionLineCreate(line_number=1,source_kind='sales_return',source_id=4,reason_code='return',replacement={'description':'other'})


def test_metadata_cannot_smuggle_monetary_changes():
    with pytest.raises(TaxInvoiceCorrectionSourceError):
        normalize_metadata_replacement('metadata_correction',{'tax_amount_delta':'0'})
    with pytest.raises(TaxInvoiceCorrectionSourceError):
        normalize_metadata_replacement('metadata_correction',{'description':'  '})
