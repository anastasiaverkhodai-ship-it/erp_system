from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.vat_declaration_export_service import (
    VatDeclarationExportError,
    export_metadata,
    render_canonical_xml,
)
from app.services.vat_declaration_form_catalog import (
    VatDeclarationFormCatalogError,
    resolve_vat_declaration_form,
)
from app.services.vat_declaration_form_mapper import (
    VatDeclarationFormMappingError,
    map_vat_declaration_to_form,
)


def declaration():
    return SimpleNamespace(
        id=41,
        company_id=7,
        reporting_year=2026,
        reporting_month=9,
        period_end=date(2026, 9, 30),
        snapshot_version=2,
        output_taxable_base=Decimal("1000.00"),
        output_vat=Decimal("200.00"),
        input_taxable_base=Decimal("500.00"),
        input_vat_credit=Decimal("100.00"),
        opening_negative_carry=Decimal("0.00"),
        vat_payable=Decimal("100.00"),
        current_period_negative=Decimal("0.00"),
        closing_negative_carry=Decimal("0.00"),
        currency_code="UAH",
    )


def company():
    return SimpleNamespace(
        id=7,
        name="ТОВ Тест",
        edrpou="12345678",
        vat_number="CURRENT-MASTER-VALUE",
    )


def policy():
    return SimpleNamespace(
        company_id=7,
        effective_from=date(2026, 9, 1),
        payer_status="vat_payer",
        vat_number="111111111111",
    )


def test_form_catalog_selects_j0200126():
    form = resolve_vat_declaration_form(
        reporting_period_end=date(2026, 9, 30),
    )

    assert form.form_code == "J0200126"
    assert form.form_version == 26
    assert form.export_format == "xml"
    assert form.official_xsd_verified is False


def test_form_catalog_fails_closed_before_supported_version():
    with pytest.raises(VatDeclarationFormCatalogError):
        resolve_vat_declaration_form(
            reporting_period_end=date(2026, 8, 31),
        )


def test_mapper_uses_dated_vat_policy_not_company_master():
    form = resolve_vat_declaration_form(
        reporting_period_end=date(2026, 9, 30),
    )

    document = map_vat_declaration_to_form(
        declaration=declaration(),
        company=company(),
        company_vat_policy=policy(),
        form=form,
    )

    assert document.company_vat_number == "111111111111"
    assert (
        document.company_vat_number
        != company().vat_number
    )


def test_mapper_fails_closed_on_cross_tenant_policy():
    form = resolve_vat_declaration_form(
        reporting_period_end=date(2026, 9, 30),
    )

    bad_policy = policy()
    bad_policy.company_id = 8

    with pytest.raises(VatDeclarationFormMappingError):
        map_vat_declaration_to_form(
            declaration=declaration(),
            company=company(),
            company_vat_policy=bad_policy,
            form=form,
        )


def test_xml_export_is_deterministic_and_hashed():
    form = resolve_vat_declaration_form(
        reporting_period_end=date(2026, 9, 30),
    )

    document = map_vat_declaration_to_form(
        declaration=declaration(),
        company=company(),
        company_vat_policy=policy(),
        form=form,
    )

    first = render_canonical_xml(
        document=document,
        form=form,
    )
    second = render_canonical_xml(
        document=document,
        form=form,
    )

    assert first == second

    metadata = export_metadata(
        document=document,
        form=form,
        payload=first,
    )

    assert metadata["form_code"] == "J0200126"
    assert metadata["form_version"] == 26
    assert metadata["official_xsd_verified"] is False
    assert len(metadata["sha256"]) == 64


def test_export_rejects_invalid_edrpou():
    form = resolve_vat_declaration_form(
        reporting_period_end=date(2026, 9, 30),
    )

    bad_company = company()
    bad_company.edrpou = "123"

    document = map_vat_declaration_to_form(
        declaration=declaration(),
        company=bad_company,
        company_vat_policy=policy(),
        form=form,
    )

    with pytest.raises(VatDeclarationExportError):
        render_canonical_xml(
            document=document,
            form=form,
        )
