from __future__ import annotations

import hashlib

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.company_vat_policy import CompanyVatPolicy
from app.models.vat_declaration import VatDeclaration
from app.models.vat_declaration_export_artifact import (
    VatDeclarationExportArtifact,
)
from app.services.vat_declaration_export_service import (
    export_metadata,
    render_canonical_xml,
)
from app.services.vat_declaration_form_catalog import (
    resolve_vat_declaration_form,
)
from app.services.vat_declaration_form_mapper import (
    map_vat_declaration_to_form,
)


def _fail(detail: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
    )


async def _load_export_inputs(
    db: AsyncSession,
    *,
    company_id: int,
    vat_declaration_id: int,
) -> tuple[
    VatDeclaration,
    Company,
    CompanyVatPolicy,
]:
    declaration = await db.scalar(
        select(VatDeclaration).where(
            VatDeclaration.company_id == company_id,
            VatDeclaration.id == vat_declaration_id,
        )
    )

    if declaration is None:
        _fail("VAT declaration not found")

    company = await db.scalar(
        select(Company).where(
            Company.id == company_id,
        )
    )

    if company is None:
        _fail("Company not found")

    policy = await db.scalar(
        select(CompanyVatPolicy)
        .where(
            CompanyVatPolicy.company_id == company_id,
            CompanyVatPolicy.effective_from
            <= declaration.period_end,
        )
        .order_by(
            CompanyVatPolicy.effective_from.desc(),
            CompanyVatPolicy.id.desc(),
        )
        .limit(1)
    )

    if policy is None:
        _fail(
            "No VAT payer policy exists for declaration period"
        )

    return declaration, company, policy


async def create_or_get_vat_declaration_export(
    db: AsyncSession,
    *,
    company_id: int,
    vat_declaration_id: int,
    created_by: int,
) -> VatDeclarationExportArtifact:
    """
    Materialize a reproducible immutable VAT declaration export.

    This service does not commit or rollback.
    The API caller owns the transaction.

    Export creation is not submission, acceptance or KEP signing.
    """

    if company_id <= 0 or created_by <= 0:
        _fail("Invalid declaration export actor/company")

    declaration, company, policy = await _load_export_inputs(
        db,
        company_id=company_id,
        vat_declaration_id=vat_declaration_id,
    )

    form = resolve_vat_declaration_form(
        reporting_period_end=declaration.period_end,
    )

    document = map_vat_declaration_to_form(
        declaration=declaration,
        company=company,
        company_vat_policy=policy,
        form=form,
    )

    payload = render_canonical_xml(
        document=document,
        form=form,
    )

    metadata = export_metadata(
        document=document,
        form=form,
        payload=payload,
    )

    digest = str(metadata["sha256"])
    size_bytes = int(metadata["size_bytes"])

    if hashlib.sha256(payload).hexdigest() != digest:
        _fail("VAT declaration export hash mismatch")

    existing = await db.scalar(
        select(VatDeclarationExportArtifact).where(
            VatDeclarationExportArtifact.company_id
            == company_id,
            VatDeclarationExportArtifact.vat_declaration_id
            == declaration.id,
            VatDeclarationExportArtifact.declaration_snapshot_version
            == declaration.snapshot_version,
            VatDeclarationExportArtifact.form_code
            == form.form_code,
            VatDeclarationExportArtifact.form_version
            == form.form_version,
            VatDeclarationExportArtifact.export_format
            == form.export_format,
        )
    )

    if existing is not None:
        if (
            existing.payload != payload
            or existing.payload_sha256 != digest
            or existing.payload_size_bytes != size_bytes
        ):
            _fail(
                "Stored VAT declaration export artifact "
                "does not match deterministic export"
            )

        return existing

    artifact = VatDeclarationExportArtifact(
        company_id=company_id,
        vat_declaration_id=declaration.id,
        declaration_snapshot_version=(
            declaration.snapshot_version
        ),
        form_code=form.form_code,
        form_version=form.form_version,
        export_format=form.export_format,
        mime_type="application/xml",
        file_name=(
            f"{form.form_code}_"
            f"{declaration.reporting_year}_"
            f"{declaration.reporting_month:02d}_"
            f"v{declaration.snapshot_version}.xml"
        ),
        payload=payload,
        payload_sha256=digest,
        payload_size_bytes=size_bytes,
        official_xsd_verified=(
            form.official_xsd_verified
        ),
        created_by=created_by,
    )

    db.add(artifact)
    await db.flush()

    return artifact


async def get_vat_declaration_export(
    db: AsyncSession,
    *,
    company_id: int,
    vat_declaration_id: int,
    export_id: int,
) -> VatDeclarationExportArtifact:
    artifact = await db.scalar(
        select(VatDeclarationExportArtifact).where(
            VatDeclarationExportArtifact.company_id
            == company_id,
            VatDeclarationExportArtifact.vat_declaration_id
            == vat_declaration_id,
            VatDeclarationExportArtifact.id == export_id,
        )
    )

    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="VAT declaration export not found",
        )

    return artifact


async def list_vat_declaration_exports(
    db: AsyncSession,
    *,
    company_id: int,
    vat_declaration_id: int,
) -> list[VatDeclarationExportArtifact]:
    declaration_exists = await db.scalar(
        select(VatDeclaration.id).where(
            VatDeclaration.company_id == company_id,
            VatDeclaration.id == vat_declaration_id,
        )
    )

    if declaration_exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="VAT declaration not found",
        )

    result = await db.scalars(
        select(VatDeclarationExportArtifact)
        .where(
            VatDeclarationExportArtifact.company_id
            == company_id,
            VatDeclarationExportArtifact.vat_declaration_id
            == vat_declaration_id,
        )
        .order_by(
            VatDeclarationExportArtifact.created_at.asc(),
            VatDeclarationExportArtifact.id.asc(),
        )
    )

    return list(result.all())
