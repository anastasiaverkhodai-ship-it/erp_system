import ast
from pathlib import Path

from app.services.payroll_payment_tax_external_signature import (
    canonical_payload,
)


def test_registry_source_is_fail_closed():
    source = Path(
        "app/services/payroll_payment_tax_receipt_registry.py"
    ).read_text()

    ast.parse(source)

    assert "verify_external_source_for_evidence(" in source
    assert "db.begin_nested()" in source
    assert '"NOT_VERIFIED"' in source
    assert '"BLOCKED"' in source


def test_registry_migration_has_global_unique():
    source = Path(
        "alembic/versions/"
        "p13receipt261108_external_receipt_registry.py"
    ).read_text()

    ast.parse(source)

    assert "UNIQUE (issuer_id, receipt_id)" in source
    assert "UNIQUE (company_id, evidence_id)" in source
    assert "trg_p13_receipt_immutable" in source
    assert "OWNER TO erp_snapshot_owner" in source
    assert "GRANT SELECT, INSERT" in source


def test_signed_payload_canonical_schema():
    data = {
        "issuer_id": "test",
        "key_id": "key",
        "receipt_id": "receipt",
        "company_id": 1,
        "payment_id": 2,
        "payment_date": "2026-09-15",
        "source_date": "2026-09-15",
        "component": "personal_income_tax",
        "amount": "18.00",
        "currency_code": "UAH",
    }

    assert isinstance(canonical_payload(data), bytes)
