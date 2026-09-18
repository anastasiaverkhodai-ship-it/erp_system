"""
Real PostgreSQL integration proof for the S11 VAT declaration
immutable export artifact.

The test uses the repository's real PostgreSQL database connection
but isolates all test data in a transaction that is rolled back.
"""

from __future__ import annotations

import hashlib
import os

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.models

from app.core.database import engine
from app.models.company import Company
from app.models.company_vat_policy import CompanyVatPolicy
from app.models.user import User
from app.models.vat_declaration import VatDeclaration
from app.models.vat_declaration_export_artifact import (
    VatDeclarationExportArtifact,
)
from app.services.vat_declaration_export_persistence_service import (
    create_or_get_vat_declaration_export,
    get_vat_declaration_export,
    list_vat_declaration_exports,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_E2E") != "1",
    reason="Set RUN_POSTGRES_E2E=1",
)


def _required_value(column):
    name = column.name
    type_name = column.type.__class__.__name__.lower()

    if name == "email":
        return "s11-export@example.test"

    if name == "name":
        return "S11 Export Company"

    if name == "effective_from":
        from datetime import date
        return date(2026, 1, 1)

    if name == "payer_status":
        return "vat_payer"

    if name == "legal_basis":
        return "S11 PostgreSQL E2E"

    if name == "allow_cash_method":
        return False

    if name == "vat_policy_enabled":
        return True

    if name == "inventory_valuation_method":
        return "fifo"

    if name == "chart_of_accounts_template":
        return "ukraine_standard"

    if name == "is_active":
        return True

    if "bool" in type_name:
        return False

    if (
        "integer" in type_name
        or "bigint" in type_name
        or "smallint" in type_name
    ):
        return 1

    if (
        "string" in type_name
        or "varchar" in type_name
        or "text" in type_name
    ):
        return f"s11-{name}"

    raise AssertionError(
        f"No safe fixture value for required column: "
        f"{column.table.name}.{name} ({column.type})"
    )


def _minimal_kwargs(model, overrides):
    table = model.__table__
    values = {}

    for column in table.columns:
        if column.primary_key:
            continue

        if column.name in overrides:
            values[column.name] = overrides[column.name]
            continue

        if column.nullable:
            continue

        if column.default is not None:
            continue

        if column.server_default is not None:
            continue

        if column.name.endswith("_id"):
            continue

        values[column.name] = _required_value(column)

    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_vat_declaration_export_real_postgresql():
    from datetime import date, datetime, timezone
    from decimal import Decimal

    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            db = AsyncSession(
                bind=connection,
                expire_on_commit=False,
            )

            # This integration test runs against a populated
            # real PostgreSQL database. Never use fixed globally
            # unique business identifiers: select deterministic
            # unused fixture identities inside this transaction.
            existing_edrpous = set(
                (
                    await db.execute(
                        select(Company.edrpou).where(
                            Company.edrpou.is_not(None)
                        )
                    )
                ).scalars().all()
            )

            test_edrpou = next(
                candidate
                for candidate in (
                    f"98{number:06d}"
                    for number in range(1000000)
                )
                if candidate not in existing_edrpous
            )

            existing_company_vat_numbers = set(
                (
                    await db.execute(
                        select(Company.vat_number).where(
                            Company.vat_number.is_not(None)
                        )
                    )
                ).scalars().all()
            )

            existing_policy_vat_numbers = set(
                (
                    await db.execute(
                        select(
                            CompanyVatPolicy.vat_number
                        ).where(
                            CompanyVatPolicy.vat_number.is_not(
                                None
                            )
                        )
                    )
                ).scalars().all()
            )

            used_vat_numbers = (
                existing_company_vat_numbers
                | existing_policy_vat_numbers
            )

            vat_candidates = (
                f"98{number:010d}"
                for number in range(10000000000)
            )

            test_company_vat = next(
                candidate
                for candidate in vat_candidates
                if candidate not in used_vat_numbers
            )

            used_vat_numbers.add(test_company_vat)

            test_policy_vat = next(
                candidate
                for candidate in vat_candidates
                if candidate not in used_vat_numbers
            )

            existing_emails = set(
                (
                    await db.execute(
                        select(User.email)
                    )
                ).scalars().all()
            )

            test_email = next(
                f"s11-export-{number}@example.test"
                for number in range(1000000)
                if (
                    f"s11-export-{number}@example.test"
                    not in existing_emails
                )
            )

            print(
                "REAL PG FIXTURE EDRPOU =",
                test_edrpou,
            )
            print(
                "REAL PG FIXTURE COMPANY VAT =",
                test_company_vat,
            )
            print(
                "REAL PG FIXTURE POLICY VAT =",
                test_policy_vat,
            )

            user = User(
                **_minimal_kwargs(
                    User,
                    {
                        "email": test_email,
                        "password_hash": "not-used",
                        "is_active": True,
                    },
                )
            )
            db.add(user)
            await db.flush()

            company = Company(
                **_minimal_kwargs(
                    Company,
                    {
                        "name": "S11 Export Company",
                        "edrpou": test_edrpou,
                        "vat_number": test_company_vat,
                        "vat_policy_enabled": True,
                        "is_active": True,
                    },
                )
            )
            db.add(company)
            await db.flush()

            policy = CompanyVatPolicy(
                **_minimal_kwargs(
                    CompanyVatPolicy,
                    {
                        "company_id": company.id,
                        "effective_from": date(
                            2026,
                            1,
                            1,
                        ),
                        "payer_status": "vat_payer",
                        "vat_number": test_policy_vat,
                        "legal_basis": (
                            "S11 PostgreSQL E2E"
                        ),
                        "allow_cash_method": False,
                        "created_by": user.id,
                    },
                )
            )
            db.add(policy)
            await db.flush()

            declaration = VatDeclaration(
                company_id=company.id,
                reporting_year=2026,
                reporting_month=9,
                period_start=date(2026, 9, 1),
                period_end=date(2026, 9, 30),
                source_cutoff_at=datetime(
                    2026,
                    10,
                    1,
                    0,
                    0,
                    tzinfo=timezone.utc,
                ),
                snapshot_version=1,
                supersedes_declaration_id=None,
                output_taxable_base=Decimal(
                    "1000.00"
                ),
                output_vat=Decimal("200.00"),
                input_taxable_base=Decimal(
                    "500.00"
                ),
                input_vat_credit=Decimal(
                    "100.00"
                ),
                opening_negative_carry=Decimal(
                    "0.00"
                ),
                vat_payable=Decimal("100.00"),
                current_period_negative=Decimal(
                    "0.00"
                ),
                closing_negative_carry=Decimal(
                    "0.00"
                ),
                currency_code="UAH",
                created_by=user.id,
            )

            db.add(declaration)
            await db.flush()

            artifact = (
                await create_or_get_vat_declaration_export(
                    db,
                    company_id=company.id,
                    vat_declaration_id=declaration.id,
                    created_by=user.id,
                )
            )

            await db.flush()

            assert artifact.id is not None
            assert artifact.company_id == company.id
            assert (
                artifact.vat_declaration_id
                == declaration.id
            )
            assert (
                artifact.declaration_snapshot_version
                == declaration.snapshot_version
            )

            assert artifact.form_code == "J0200126"
            assert artifact.form_version == 26
            assert artifact.export_format == "xml"
            assert (
                artifact.mime_type
                == "application/xml"
            )
            assert (
                artifact.official_xsd_verified
                is False
            )

            assert artifact.payload
            assert (
                artifact.payload_size_bytes
                == len(artifact.payload)
            )
            assert (
                hashlib.sha256(
                    artifact.payload
                ).hexdigest()
                == artifact.payload_sha256
            )

            assert test_edrpou.encode() in artifact.payload

            # Historical/dated VAT identity must be used.
            assert test_policy_vat.encode() in artifact.payload
            assert test_company_vat.encode() not in artifact.payload

            first_id = artifact.id
            first_payload = bytes(artifact.payload)
            first_hash = artifact.payload_sha256

            repeated = (
                await create_or_get_vat_declaration_export(
                    db,
                    company_id=company.id,
                    vat_declaration_id=declaration.id,
                    created_by=user.id,
                )
            )

            assert repeated.id == first_id
            assert repeated.payload == first_payload
            assert (
                repeated.payload_sha256
                == first_hash
            )

            count = await db.scalar(
                select(func.count())
                .select_from(
                    VatDeclarationExportArtifact
                )
                .where(
                    VatDeclarationExportArtifact.company_id
                    == company.id,
                    VatDeclarationExportArtifact.vat_declaration_id
                    == declaration.id,
                )
            )

            assert count == 1

            listed = (
                await list_vat_declaration_exports(
                    db,
                    company_id=company.id,
                    vat_declaration_id=declaration.id,
                )
            )

            assert len(listed) == 1
            assert listed[0].id == first_id

            loaded = (
                await get_vat_declaration_export(
                    db,
                    company_id=company.id,
                    vat_declaration_id=declaration.id,
                    export_id=first_id,
                )
            )

            assert loaded.payload == first_payload
            assert loaded.payload_sha256 == first_hash

            # Tenant isolation must fail closed.
            with pytest.raises(HTTPException):
                await get_vat_declaration_export(
                    db,
                    company_id=company.id + 999999,
                    vat_declaration_id=declaration.id,
                    export_id=first_id,
                )

            # Stored artifact must remain immutable even if
            # mutable company master data later changes.
            company.name = "Changed After Export"
            company.vat_number = "222222222222"
            await db.flush()

            db.expire(loaded)

            stored = await db.scalar(
                select(
                    VatDeclarationExportArtifact
                ).where(
                    VatDeclarationExportArtifact.id
                    == first_id
                )
            )

            assert stored is not None
            assert stored.payload == first_payload
            assert (
                stored.payload_sha256
                == first_hash
            )

            print(
                "REAL PG EXPORT ID =",
                first_id,
            )
            print(
                "REAL PG EXPORT SHA256 =",
                first_hash,
            )
            print(
                "REAL PG EXPORT SIZE =",
                len(first_payload),
            )
            print(
                "REAL PG DETERMINISTIC REUSE = PASS"
            )
            print(
                "REAL PG DATED VAT IDENTITY = PASS"
            )
            print(
                "REAL PG TENANT ISOLATION = PASS"
            )
            print(
                "REAL PG STORED IMMUTABILITY = PASS"
            )

            await db.close()

        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_export_table_constraints_real_postgresql():
    from sqlalchemy import text

    async with engine.connect() as connection:
        names = set(
            (
                await connection.execute(
                    text(
                        """
                        SELECT conname
                        FROM pg_constraint
                        WHERE conrelid =
                          'vat_declaration_export_artifacts'
                          ::regclass
                        """
                    )
                )
            ).scalars().all()
        )

        expected = {
            "pk_vat_declaration_export_artifacts",
            "uq_vdea_identity",
            "ck_vdea_snapshot_version_pos",
            "ck_vdea_form_version_pos",
            "ck_vdea_payload_size_pos",
            "ck_vdea_sha256_len",
            "fk_vdea_company",
            "fk_vdea_declaration",
            "fk_vdea_created_by",
        }

        assert expected.issubset(names)

        print(
            "REAL PG EXPORT CONSTRAINTS = PASS"
        )
