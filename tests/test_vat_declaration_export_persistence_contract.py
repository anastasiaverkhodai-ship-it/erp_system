from pathlib import Path

from fastapi.routing import APIRoute

import app.models
from app.api.v1.vat_declarations import router
from app.core.database import Base
from app.main import app
from app.models.vat_declaration_export_artifact import (
    VatDeclarationExportArtifact,
)


ROOT = "/companies/{company_id}/vat-declarations"
EXPORTS = ROOT + "/{declaration_id}/exports"
EXPORT = EXPORTS + "/{export_id}"
CONTENT = EXPORT + "/content"

API_EXPORTS = "/api/v1" + EXPORTS
API_EXPORT = "/api/v1" + EXPORT
API_CONTENT = "/api/v1" + CONTENT


def test_export_artifact_registered_in_metadata():
    assert (
        "vat_declaration_export_artifacts"
        in Base.metadata.tables
    )

    table = Base.metadata.tables[
        "vat_declaration_export_artifacts"
    ]

    expected = {
        "id",
        "company_id",
        "vat_declaration_id",
        "declaration_snapshot_version",
        "form_code",
        "form_version",
        "export_format",
        "mime_type",
        "file_name",
        "payload",
        "payload_sha256",
        "payload_size_bytes",
        "official_xsd_verified",
        "created_by",
        "created_at",
    }

    assert set(table.columns.keys()) == expected


def test_export_artifact_model_is_separate_from_declaration():
    assert (
        VatDeclarationExportArtifact.__tablename__
        == "vat_declaration_export_artifacts"
    )


def test_export_router_contract():
    routes = {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert (EXPORTS, "POST") in routes
    assert (EXPORTS, "GET") in routes
    assert (EXPORT, "GET") in routes
    assert (CONTENT, "GET") in routes


def test_export_openapi_contract():
    paths = app.openapi()["paths"]

    assert "post" in paths[API_EXPORTS]
    assert "get" in paths[API_EXPORTS]
    assert "get" in paths[API_EXPORT]
    assert "get" in paths[API_CONTENT]


def test_export_content_openapi_declares_xml():
    operation = app.openapi()["paths"][
        API_CONTENT
    ]["get"]

    assert (
        "application/xml"
        in operation["responses"]["200"]["content"]
    )


def test_export_service_has_no_transaction_ownership():
    text = Path(
        "app/services/"
        "vat_declaration_export_persistence_service.py"
    ).read_text(encoding="utf-8")

    assert ".commit(" not in text
    assert ".rollback(" not in text


def test_export_api_owns_write_transaction():
    text = Path(
        "app/api/v1/vat_declarations.py"
    ).read_text(encoding="utf-8")

    assert (
        "create_or_get_vat_declaration_export("
        in text
    )
    assert "await db.commit()" in text
    assert "await db.rollback()" in text


def test_export_does_not_claim_submission_or_acceptance():
    text = Path(
        "app/services/"
        "vat_declaration_export_persistence_service.py"
    ).read_text(encoding="utf-8")

    assert (
        "Export creation is not submission"
        in text
    )


def test_xsd_claim_remains_fail_closed():
    text = Path(
        "app/services/vat_declaration_form_catalog.py"
    ).read_text(encoding="utf-8")

    assert "official_xsd_verified=False" in text


def test_migration_chain():
    text = Path(
        "alembic/versions/"
        "d4a1b2c3e586_add_vat_declaration_export_artifacts.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "d4a1b2c3e586"' in text
    assert 'down_revision = "c3f0a1b2d475"' in text
    assert "vat_declaration_export_artifacts" in text
    assert "uq_vdea_identity" in text


def test_export_identity_is_input_based_not_payload_based():
    from app.models.vat_declaration_export_artifact import (
        VatDeclarationExportArtifact,
    )

    identity = next(
        constraint
        for constraint
        in VatDeclarationExportArtifact.__table__.constraints
        if (
            constraint.__class__.__name__
            == "UniqueConstraint"
            and constraint.name == "uq_vdea_identity"
        )
    )

    columns = tuple(
        column.name
        for column in identity.columns
    )

    assert columns == (
        "company_id",
        "vat_declaration_id",
        "declaration_snapshot_version",
        "form_code",
        "form_version",
        "export_format",
    )

    assert "payload_sha256" not in columns


def test_existing_export_lookup_detects_changed_output():
    from pathlib import Path

    text = Path(
        "app/services/"
        "vat_declaration_export_persistence_service.py"
    ).read_text(encoding="utf-8")

    assert (
        "VatDeclarationExportArtifact."
        "declaration_snapshot_version"
        in text
    )

    assert (
        "VatDeclarationExportArtifact.payload_sha256"
        "            == digest"
        not in text
    )

    assert "existing.payload_sha256 != digest" in text
    assert "existing.payload != payload" in text
