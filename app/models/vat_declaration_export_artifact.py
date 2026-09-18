from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VatDeclarationExportArtifact(Base):
    """
    Immutable reproducible export of one immutable VAT declaration snapshot.

    An artifact records exactly which form contract and canonical payload
    were used. Export generation does not mean DPS submission or acceptance.
    """

    __tablename__ = "vat_declaration_export_artifacts"

    __table_args__ = (
        CheckConstraint(
            "declaration_snapshot_version > 0",
            name="ck_vdea_snapshot_version_pos",
        ),
        CheckConstraint(
            "form_version > 0",
            name="ck_vdea_form_version_pos",
        ),
        CheckConstraint(
            "payload_size_bytes > 0",
            name="ck_vdea_payload_size_pos",
        ),
        CheckConstraint(
            "char_length(payload_sha256) = 64",
            name="ck_vdea_sha256_len",
        ),
        UniqueConstraint(
            "company_id",
            "vat_declaration_id",
            "declaration_snapshot_version",
            "form_code",
            "form_version",
            "export_format",
            name="uq_vdea_identity",
        ),
        Index(
            "ix_vdea_company",
            "company_id",
        ),
        Index(
            "ix_vdea_declaration",
            "vat_declaration_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    company_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    vat_declaration_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "vat_declarations.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    declaration_snapshot_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    form_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    form_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    export_format: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    payload: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )

    payload_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    payload_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    official_xsd_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    created_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
