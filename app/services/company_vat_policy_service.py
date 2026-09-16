"""VAT configuration validation. Caller owns the transaction.

The first policy activates strict validation. Existing companies remain explicitly
legacy/unconfigured until migrated; no registration status is guessed for them.
"""
from sqlalchemy import select, func
from app.models.company import Company
from app.models.company_vat_policy import CompanyVatPolicy
from app.models.trade_document import TradeDocument
from app.schemas.company_vat_policy import CompanyVatPolicyCreate
from app.services.tax_recognition_types import TaxRecognitionMethod


class VatPolicyError(ValueError):
    pass


async def append_vat_policy(db, *, company_id, data, created_by):
    data = CompanyVatPolicyCreate.model_validate(data)
    company = await db.scalar(select(Company).where(Company.id == company_id, Company.is_active.is_(True))
                              .with_for_update().execution_options(populate_existing=True))
    if company is None:
        raise VatPolicyError('Active company not found')
    policies = list((await db.scalars(select(CompanyVatPolicy).where(CompanyVatPolicy.company_id == company_id)
                                     .order_by(CompanyVatPolicy.effective_from))).all())
    for policy in policies:
        if policy.effective_from == data.effective_from:
            if all(getattr(policy, key) == value for key, value in data.model_dump().items()):
                return policy
            raise VatPolicyError('Policy date already exists with different terms')
    if policies and data.effective_from <= policies[-1].effective_from:
        raise VatPolicyError('Policies must be appended in increasing effective-date order')
    latest = await db.scalar(select(func.max(TradeDocument.document_date)).where(
        TradeDocument.company_id == company_id, TradeDocument.confirmed_at.is_not(None)))
    if latest is not None and data.effective_from <= latest:
        raise VatPolicyError('Policy cannot change the date range of previously confirmed documents')
    policy = CompanyVatPolicy(company_id=company_id, created_by=created_by, **data.model_dump())
    db.add(policy)
    company.vat_policy_enabled = True
    await db.flush()
    return policy


def validate_line_vat_policy(*, policy, line):
    code = getattr(line, 'tax_rate_code', None)
    basis = getattr(line, 'tax_legal_basis', None)
    reason = getattr(line, 'no_vat_reason', None)
    method = getattr(line, 'tax_recognition_method', None)
    if code is None:
        if not basis or not basis.strip() or reason != 'non_vat_payer':
            raise VatPolicyError('Missing VAT classification: specify a rate/category or non_vat_payer with legal basis')
        if policy.payer_status != 'non_vat_payer':
            raise VatPolicyError('VAT payer cannot use non_vat_payer classification')
        return
    if reason is not None:
        raise VatPolicyError('A VAT category cannot be combined with no_vat_reason')
    if policy.payer_status != 'vat_payer':
        raise VatPolicyError('VAT-configured operations of a non-payer require a separate gross-cost workflow')
    if code != 'VAT20' and (not basis or not basis.strip()):
        raise VatPolicyError('Special rate/category requires an explicit legal basis')
    if method == TaxRecognitionMethod.CASH_METHOD:
        if not policy.allow_cash_method or not basis or not basis.strip():
            raise VatPolicyError('Cash method requires company authorization and an operation-specific legal basis')
    elif method == TaxRecognitionMethod.MANUAL and code not in {'VAT_EXEMPT', 'VAT_OUT_OF_SCOPE'}:
        raise VatPolicyError('Manual taxable recognition is not supported by strict VAT policy')


async def validate_document_vat_policy(db, *, company, document):
    if not getattr(company, 'vat_policy_enabled', False):
        return  # Explicit migration state; exposed by the settings API.
    policy = await db.scalar(select(CompanyVatPolicy).where(
        CompanyVatPolicy.company_id == company.id,
        CompanyVatPolicy.effective_from <= document.document_date,
    ).order_by(CompanyVatPolicy.effective_from.desc()).limit(1))
    if policy is None:
        raise VatPolicyError('No VAT policy effective on document date')
    from app.models.counterparty_vat_registration import CounterpartyVatRegistration
    from app.services.trade_document_types import TradeDirection
    party = await db.scalar(select(CounterpartyVatRegistration).where(
        CounterpartyVatRegistration.company_id == company.id,
        CounterpartyVatRegistration.counterparty_id == document.counterparty_id,
        CounterpartyVatRegistration.effective_from <= document.document_date,
    ).order_by(CounterpartyVatRegistration.effective_from.desc()).limit(1))
    if party is None:
        raise VatPolicyError('No counterparty VAT registration effective on document date')
    from app.services.invoice_tax_calculation_service import calculate_invoice_line_tax, InvoiceTaxConfigurationError
    for line in document.lines:
        if document.direction == TradeDirection.PURCHASE:
            # A taxable supplier invoice cannot create input credit for a non-payer.
            if policy.payer_status != 'vat_payer' and getattr(line, 'tax_rate_code', None) in {'VAT20', 'VAT7', 'VAT14'}:
                raise VatPolicyError('Non-payer purchase VAT requires a separate gross-cost workflow')
            from types import SimpleNamespace
            seller_policy = SimpleNamespace(payer_status=party.payer_status, allow_cash_method=policy.allow_cash_method)
            validate_line_vat_policy(policy=seller_policy, line=line)
        else:
            validate_line_vat_policy(policy=policy, line=line)
        try:
            calculate_invoice_line_tax(document=document, line=line)
        except InvoiceTaxConfigurationError as exc:
            raise VatPolicyError(str(exc)) from exc
    # A durable reference prevents later settings from being mistaken for this snapshot.
    document.vat_policy_id = policy.id
    document.counterparty_vat_registration_id = party.id


async def append_counterparty_vat_registration(db, *, company_id, counterparty_id, data, created_by):
    from app.models.counterparty import Counterparty
    from app.models.counterparty_vat_registration import CounterpartyVatRegistration
    from app.schemas.company_vat_policy import CounterpartyVatRegistrationCreate
    data = CounterpartyVatRegistrationCreate.model_validate(data)
    company = await db.scalar(select(Company).where(Company.id == company_id, Company.is_active.is_(True))
                              .with_for_update().execution_options(populate_existing=True))
    party = await db.scalar(select(Counterparty).where(Counterparty.company_id == company_id,
        Counterparty.id == counterparty_id, Counterparty.is_active.is_(True)))
    if company is None or party is None:
        raise VatPolicyError('Active company/counterparty not found')
    records = list((await db.scalars(select(CounterpartyVatRegistration).where(
        CounterpartyVatRegistration.company_id == company_id,
        CounterpartyVatRegistration.counterparty_id == counterparty_id,
    ).order_by(CounterpartyVatRegistration.effective_from))).all())
    for record in records:
        if record.effective_from == data.effective_from:
            if all(getattr(record, key) == value for key, value in data.model_dump().items()):
                return record
            raise VatPolicyError('Registration date already exists with different terms')
    if records and data.effective_from <= records[-1].effective_from:
        raise VatPolicyError('Registrations must be appended in increasing effective-date order')
    latest = await db.scalar(select(func.max(TradeDocument.document_date)).where(
        TradeDocument.company_id == company_id, TradeDocument.counterparty_id == counterparty_id,
        TradeDocument.confirmed_at.is_not(None)))
    if latest is not None and data.effective_from <= latest:
        raise VatPolicyError('Registration cannot change previously confirmed documents')
    record = CounterpartyVatRegistration(company_id=company_id, counterparty_id=counterparty_id,
                                         created_by=created_by, **data.model_dump())
    db.add(record)
    await db.flush()
    return record
