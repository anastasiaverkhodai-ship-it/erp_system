"""Read-only reproducer for P10-01; run with python -m scripts.audit_phase10_first_event.

One invoice line: net 100, VAT 20. Payment 72 gross covers net 60;
a subsequent shipment covers net 70 of the SAME goods. The second event
adds net 10/VAT 2, so cumulative VAT must be 14, not 20.
Exit 1 means the documented defect remains; this is not a passing regression test.
No database access or mutations. See docs/phase10_1_audit.md.
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.input_tax_recognition_calculation_service import calculate_input_tax_recognition_limit
from app.services.tax_recognition_orchestration_service import (
    build_fulfillment_recognition_candidate,
    build_output_tax_recognition_targets,
    build_settlement_recognition_candidate,
)
from app.services.tax_recognition_types import TaxRecognitionMethod
from app.services.tax_types import TaxDirection, TaxType


def main():
    failures = 0
    for direction in (TaxDirection.OUTPUT, TaxDirection.INPUT):
        for payment_first in (True, False):
            calculation = SimpleNamespace(
                id=10, company_id=1, tax_type=TaxType.VAT, direction=direction,
                recognition_method=TaxRecognitionMethod.FIRST_EVENT,
                taxable_base=Decimal('100.00'), tax_amount=Decimal('20.00'), currency_code='UAH',
            )
            d1, d2 = date(2026, 9, 1), date(2026, 9, 2)
            payment = build_settlement_recognition_candidate(
                calculation=calculation, source_id=20, event_date=d1 if payment_first else d2,
                settlement_amount=Decimal('72.00'), invoice_total_amount=Decimal('120.00'),
            )
            shipment = build_fulfillment_recognition_candidate(
                calculation=calculation, source_id=30, event_date=d2 if payment_first else d1,
                allocation_quantity=Decimal('7'), invoice_line_quantity=Decimal('10'),
            )
            if direction == TaxDirection.OUTPUT:
                targets = build_output_tax_recognition_targets(calculation=calculation, candidates=(payment, shipment))
                base = sum((t.taxable_base for t in targets), Decimal(0))
                tax = sum((t.tax_amount for t in targets), Decimal(0))
            else:
                # Deliberately generous evidence isolates the economic-capacity bug.
                # Evidence is a capacity ceiling, not an additional economic event.
                evidence = SimpleNamespace(
                    id=40, company_id=1, tax_calculation_id=10,
                    evidence_type='registered_tax_invoice', evidence_number='AUDIT-10.1',
                    evidence_date=d1, credit_available_date=d1, effective_date=d1,
                    evidenced_taxable_base=Decimal('100.00'), evidenced_tax_amount=Decimal('20.00'),
                    currency_code='UAH', reversal_of_id=None,
                )
                result = calculate_input_tax_recognition_limit(
                    calculation=calculation, economic_candidates=(payment, shipment),
                    evidence_events=(evidence,), as_of_date=d2,
                )
                base, tax = result.recognizable_taxable_base, result.recognizable_tax_amount
            ok = (base, tax) == (Decimal('70.00'), Decimal('14.00'))
            failures += not ok
            print(f'{direction.value} payment_first={payment_first}: '
                  f'actual base={base} VAT={tax}; expected base=70.00 VAT=14.00; '
                  f'{"PASS" if ok else "DEFECT P10-01"}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
