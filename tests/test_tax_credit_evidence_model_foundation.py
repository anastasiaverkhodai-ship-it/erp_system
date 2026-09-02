from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)

from app.models.tax_credit_evidence import (
    TaxCreditEvidence,
)
from app.services.tax_credit_evidence_types import (
    TaxCreditEvidenceType,
)


EXPECTED_COLUMNS = {
    "id",
    "company_id",
    "tax_calculation_id",
    "evidence_type",
    "evidence_number",
    "evidence_date",
    "credit_available_date",
    "effective_date",
    "evidenced_taxable_base",
    "evidenced_tax_amount",
    "currency_code",
    "created_by",
    "created_at",
    "reversal_of_id",
}


def test_tax_credit_evidence_table_name_and_columns():
    table = TaxCreditEvidence.__table__

    assert (
        table.name
        == "tax_credit_evidence"
    )

    assert set(
        table.columns.keys()
    ) == EXPECTED_COLUMNS


def test_tax_credit_evidence_unique_constraints():
    table = TaxCreditEvidence.__table__

    names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "uq_tax_credit_evidence_company_id_id"
        in names
    )

    assert (
        "uq_tax_credit_evidence_"
        "company_id_id_tax_calculation"
        in names
    )

    assert (
        "uq_tax_credit_evidence_reversal_of"
        in names
    )


def test_tax_credit_evidence_foreign_keys():
    table = TaxCreditEvidence.__table__

    names = {
        constraint.name
        for constraint
        in table.foreign_key_constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    }

    assert (
        "fk_tax_credit_evidence_"
        "company_tax_calculation"
        in names
    )

    assert (
        "fk_tax_credit_evidence_"
        "company_reversal_tax_calculation"
        in names
    )


def test_tax_credit_evidence_checks():
    table = TaxCreditEvidence.__table__

    names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert {
        "ck_tax_credit_evidence_evidence_type",
        (
            "ck_tax_credit_evidence_"
            "evidence_number_nonempty"
        ),
        (
            "ck_tax_credit_evidence_"
            "taxable_base_nonnegative"
        ),
        (
            "ck_tax_credit_evidence_"
            "tax_amount_positive"
        ),
        (
            "ck_tax_credit_evidence_"
            "currency_code_length"
        ),
        (
            "ck_tax_credit_evidence_"
            "not_self_reversal"
        ),
    }.issubset(
        names
    )


def test_tax_credit_evidence_partial_source_index():
    table = TaxCreditEvidence.__table__

    indexes = {
        index.name: index
        for index in table.indexes
        if isinstance(
            index,
            Index,
        )
    }

    index = indexes[
        "ix_tax_credit_evidence_source_original"
    ]

    assert index.unique is False

    assert [
        column.name
        for column in index.columns
    ] == [
        "company_id",
        "tax_calculation_id",
        "evidence_type",
        "evidence_number",
        "id",
    ]

    where = str(
        index.dialect_options[
            "postgresql"
        ][
            "where"
        ]
    )

    assert (
        "reversal_of_id IS NULL"
        in where
    )


def test_tax_credit_evidence_types_are_stable():
    assert {
        item.value
        for item in TaxCreditEvidenceType
    } == {
        "registered_tax_invoice",
        "registered_adjustment",
        "customs_declaration",
        "article_201_11_document",
        "nonresident_self_invoice",
    }
