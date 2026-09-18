from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_return_input_vat_credit_correction_event import (
    PurchaseReturnInputVatCreditCorrectionEvent,
)
from app.models.purchase_value_correction_input_vat_credit_correction_event import (
    PurchaseValueCorrectionInputVatCreditCorrectionEvent,
)
from app.models.sales_return_recognition_event import (
    SalesReturnRecognitionEvent,
)
from app.models.tax_calculation import TaxCalculation
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.models.trade_value_correction_event import (
    TradeValueCorrectionEvent,
)


MONEY_Q2 = Decimal("0.01")
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class VatDeclarationSource:
    source_kind: str
    source_id: int
    economic_effective_date: date
    direction: str
    taxable_base_delta: Decimal
    tax_amount_delta: Decimal
    currency_code: str
    source_reversal_of_id: int | None = None
    tax_rate_code: str | None = None
    tax_rate: Decimal | None = None


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(
        MONEY_Q2,
        rounding=ROUND_HALF_UP,
    )


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _fail(detail: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
    )


def _require_uah(*values: str) -> None:
    if any(value != "UAH" for value in values):
        _fail("VAT declaration V1 supports UAH sources only")


def _sign(reversal_of_id: int | None) -> Decimal:
    return Decimal("-1") if reversal_of_id is not None else Decimal("1")


def _month_contains(
    effective_date: date,
    period_start: date,
    period_end: date,
) -> bool:
    return period_start <= effective_date <= period_end


async def load_vat_declaration_sources(
    *, db: AsyncSession, company_id: int, period_start: date,
    period_end: date, source_cutoff_at: datetime,
) -> list[VatDeclarationSource]:
    """Use exactly the register's single-statement database snapshot and GL controls."""
    from datetime import timezone
    from app.services.vat_register_service import load_snapshot, build_vat_register, instant
    if source_cutoff_at.tzinfo is None or source_cutoff_at.utcoffset() is None:
        _fail("VAT source cutoff must include timezone")
    if source_cutoff_at > datetime.now(timezone.utc):
        _fail("Future VAT source cutoff is not supported")
    snapshot = await load_snapshot(db, company_id=company_id)
    report = build_vat_register(snapshot, company_id=company_id, date_from=period_start,
        date_to=period_end, as_of=source_cutoff_at)
    # Drafts can expose missing legal documents; never hide broken accounting.
    informational = {"missing_tax_document", "source_document_not_registered",
        "pending_output_vat_decrease", "unclassified_tax_settlement_journal", "document_not_registered"}
    blocking = [i for i in report['issues'] if i['code'] not in informational]
    if blocking:
        _fail("VAT source control failed: " + ', '.join(sorted({i['code'] for i in blocking})))
    table_by_kind = {
        'output_tax_recognition':'tax_recognition_events',
        'input_tax_recognition':'tax_recognition_events',
        'sales_return':'sales_return_recognition_events',
        'sales_value_correction':'trade_value_correction_events',
        'purchase_return_input_credit_correction':'purchase_return_input_vat_credit_correction_events',
        'purchase_value_input_credit_correction':'purchase_value_correction_input_vat_credit_correction_events',
    }
    indexes = {t:{r['id']:r for r in rows if not r.get('created_at') or instant(r['created_at'])<=source_cutoff_at}
        for t,rows in snapshot.items()}
    sources=[]
    for event in report['events']:
        if not period_start<=event['effective_date']<=period_end:
            continue
        if not event['taxable_base'] and not event['expected_vat']:
            continue
        row=indexes[table_by_kind[event['source_kind']]][event['source_id']]
        _require_uah(row['currency_code'])
        calc=indexes['tax_calculations'].get(row.get('tax_calculation_id'),{})
        if event['source_kind'] in ('sales_return','sales_value_correction'):
            field='sales_return_recognition_event_id' if event['source_kind']=='sales_return' else 'trade_value_correction_event_id'
            lines=[r for r in indexes['tax_invoice_correction_lines'].values() if r.get(field)==row['id']]
            if len(lines)!=1:
                _fail("OUTPUT correction requires one canonical RK line")
            calc=lines[0]
        sources.append(VatDeclarationSource(source_kind=event['source_kind'],source_id=event['source_id'],
            economic_effective_date=event['effective_date'],direction=event['direction'],
            taxable_base_delta=_money(event['taxable_base']),tax_amount_delta=_money(event['expected_vat']),
            currency_code='UAH',source_reversal_of_id=row.get('reversal_of_id'),
            tax_rate_code=calc.get('tax_rate_code'),tax_rate=Decimal(str(calc['tax_rate'])) if calc.get('tax_rate') is not None else None))
    return sorted(sources,key=lambda s:(s.economic_effective_date,s.source_kind,s.source_id))
