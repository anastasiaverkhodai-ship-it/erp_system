from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as NS
import pytest
from pydantic import ValidationError
from app.schemas.company_vat_policy import CompanyVatPolicyCreate
from app.schemas.trade_document import TradeDocumentLineCreate
from app.services.company_vat_policy_service import validate_line_vat_policy, VatPolicyError
from app.services.invoice_tax_calculation_service import calculate_invoice_line_tax, build_invoice_tax_calculation
from app.services.tax_rate_definition import TaxRateDefinition
from app.services.tax_rate_catalog import TaxRateCatalog, TaxRateNotFoundError
from app.services.tax_types import TaxType
from app.services.tax_treatment_types import TaxTreatment


def line(**updates):
    values = dict(tax_rate_code='VAT20', tax_recognition_method='first_event', tax_price_mode='exclusive',
                  tax_legal_basis=None, no_vat_reason=None, quantity=D('3'), unit_price=D('0.17'))
    return NS(**(values | updates))


def policy(status='vat_payer', cash=False):
    return NS(payer_status=status, allow_cash_method=cash)


@pytest.mark.parametrize('rate', ['NaN', 'Infinity', '-Infinity', '-0.1', '1.1'])
def test_nonfinite_and_invalid_rates_rejected(rate):
    with pytest.raises(ValueError):
        TaxRateDefinition('X', TaxType.VAT, D(rate), date(2026,1,1))


def test_rate_end_is_inclusive_and_expired_version_never_revives_old_rate():
    catalog = TaxRateCatalog((TaxRateDefinition('X', TaxType.VAT, D('.2'), date(2025,1,1)),
        TaxRateDefinition('X', TaxType.VAT, D('.07'), date(2026,1,1), effective_until=date(2026,1,31))))
    assert catalog.get_effective('X', date(2026,1,31)).rate == D('.07')
    with pytest.raises(TaxRateNotFoundError):
        catalog.get_effective('X', date(2026,2,1))
    with pytest.raises(ValueError):
        TaxRateDefinition('X', TaxType.VAT, D('.2'), date(2026,1,2), effective_until=date(2026,1,1))


@pytest.mark.parametrize('code,treatment', [('VAT0', TaxTreatment.ZERO_RATED), ('VAT_EXEMPT', TaxTreatment.EXEMPT), ('VAT_OUT_OF_SCOPE', TaxTreatment.OUT_OF_SCOPE)])
def test_zero_exempt_and_outside_remain_distinct(code, treatment):
    item=line(tax_rate_code=code, tax_legal_basis='Documented statutory basis',
              tax_recognition_method='first_event' if code == 'VAT0' else 'manual')
    doc=NS(document_date=date(2026,9,16), currency_code='UAH')
    result=calculate_invoice_line_tax(document=doc, line=item)
    assert result.tax_rate.treatment == treatment
    assert result.tax_amount == 0 and result.gross_amount == D('.51')
    if code != 'VAT0':
        assert build_invoice_tax_calculation(document=doc, line=item) is None


@pytest.mark.parametrize('code', ['VAT7','VAT14','VAT0','VAT_EXEMPT','VAT_OUT_OF_SCOPE'])
def test_special_rate_needs_basis(code):
    with pytest.raises(VatPolicyError, match='basis'):
        validate_line_vat_policy(policy=policy(), line=line(tax_rate_code=code))


def test_cash_method_needs_company_authorization_and_line_basis():
    for cash, basis in [(False,'187.10 / applicable operation'),(True,None)]:
        with pytest.raises(VatPolicyError, match='Cash method'):
            validate_line_vat_policy(policy=policy(cash=cash), line=line(tax_recognition_method='cash_method', tax_legal_basis=basis))
    validate_line_vat_policy(policy=policy(cash=True), line=line(tax_recognition_method='cash_method',tax_legal_basis='187.10 / applicable operation'))


def test_nonpayer_is_explicit_and_cannot_create_vat_credit():
    no_tax=line(tax_rate_code=None, tax_recognition_method=None, tax_price_mode=None,
                no_vat_reason='non_vat_payer', tax_legal_basis='Registration extract')
    validate_line_vat_policy(policy=policy('non_vat_payer'), line=no_tax)
    with pytest.raises(VatPolicyError):
        validate_line_vat_policy(policy=policy(), line=no_tax)
    with pytest.raises(VatPolicyError):
        validate_line_vat_policy(policy=policy('non_vat_payer'), line=line())
    with pytest.raises(VatPolicyError, match='classification'):
        validate_line_vat_policy(policy=policy(), line=line(tax_rate_code=None))


@pytest.mark.parametrize('kwargs', [dict(payer_status='vat_payer'), dict(payer_status='non_vat_payer',vat_number='123'), dict(payer_status='non_vat_payer',allow_cash_method=True), dict(payer_status='non_vat_payer', legal_basis='  ')])
def test_policy_does_not_guess_registration(kwargs):
    with pytest.raises(ValidationError):
        CompanyVatPolicyCreate.model_validate(dict(effective_from='2026-09-16', legal_basis='Extract') | kwargs)


def test_api_rejects_unexplained_category_and_conflicting_no_vat_reason():
    base=dict(product_id=1, quantity='1', unit_price='10')
    for extra in [dict(no_vat_reason='non_vat_payer'),dict(tax_rate_code='VAT_EXEMPT',tax_recognition_method='first_event',tax_price_mode='exclusive',tax_legal_basis='197'),dict(tax_legal_basis='   ')]:
        with pytest.raises(ValidationError):
            TradeDocumentLineCreate.model_validate(base | extra)


@pytest.mark.parametrize('mode,price,base,tax,gross', [('exclusive','0.17','.51','.10','.61'),('inclusive','0.17','.43','.08','.51')])
def test_cents_preserve_gross_equals_base_plus_tax(mode, price, base, tax, gross):
    result=calculate_invoice_line_tax(document=NS(document_date=date(2026,9,16),currency_code='UAH'),line=line(tax_price_mode=mode,unit_price=D(price)))
    assert (result.taxable_base,result.tax_amount,result.gross_amount) == (D(base),D(tax),D(gross))
    assert result.taxable_base + result.tax_amount == result.gross_amount
