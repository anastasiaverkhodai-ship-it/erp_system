import ast
from pathlib import Path


REVISION = "f4c2a91b7e63"
DOWN_REVISION = "d91f6a2c4e70"

MIGRATION = Path(
    "alembic/versions/"
    "f4c2a91b7e63_add_warehouse_transfer_and_inventory_count.py"
)


def _source() -> str:
    return MIGRATION.read_text()


def test_migration_exists_and_parses():
    assert MIGRATION.is_file()
    ast.parse(_source())


def test_revision_chain_is_exact():
    namespace = {}

    tree = ast.parse(_source())

    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue

        if not isinstance(node.target, ast.Name):
            continue

        if node.target.id not in {
            "revision",
            "down_revision",
        }:
            continue

        namespace[node.target.id] = ast.literal_eval(
            node.value
        )

    assert namespace["revision"] == REVISION
    assert namespace["down_revision"] == DOWN_REVISION


def test_upgrade_creates_exact_domain_tables():
    text = _source()

    for table in (
        "warehouse_transfer_events",
        "warehouse_transfer_lines",
        "warehouse_transfer_valuation_layers",
        "inventory_count_events",
        "inventory_count_variance_events",
    ):
        assert f'op.create_table(\n        "{table}"' in text


def test_downgrade_drops_all_domain_tables():
    text = _source()

    for table in (
        "warehouse_transfer_events",
        "warehouse_transfer_lines",
        "warehouse_transfer_valuation_layers",
        "inventory_count_events",
        "inventory_count_variance_events",
    ):
        assert (
            'op.drop_table(\n'
            f'        "{table}"'
        ) in text


def test_transfer_database_provenance_constraints_exist():
    text = _source()

    required = {
        "fk_wte_company_source_warehouse",
        "fk_wte_company_destination_warehouse",
        "fk_wte_history_reversal_of",
        "uq_wte_history_identity",
        "ck_wte_history_key_length",
        "fk_wtl_transfer_parent_identity",
        "fk_wtl_company_issue_document",
        "fk_wtl_company_receipt_document",
        "fk_wtl_issue_document_line",
        "ck_wtl_issue_document_type",
        "ck_wtl_receipt_document_type",
        "ck_wtl_quantity_positive",
        "uq_wtl_issue_document_line",
    }

    for name in required:
        assert name in text


def test_count_database_provenance_constraints_exist():
    text = _source()

    required = {
        "fk_icev_company_product",
        "fk_icev_company_warehouse",
        "fk_icev_history_reversal_of",
        "uq_icev_history_identity",
        "ck_icev_history_key_length",
        "fk_icve_count_stock_identity",
        "fk_icve_company_adjustment_document",
        "fk_icve_adjustment_document_line",
        "ck_icve_document_type_adjustment",
        "ck_icve_quantity_positive",
        "uq_icve_inventory_count_event",
        "uq_icve_document_line",
    }

    for name in required:
        assert name in text


def test_schema_contains_no_transfer_document_type():
    text = _source()

    assert '"transfer"' not in text
    assert "'transfer'" not in text


def test_migration_does_not_modify_historical_inventory_tables():
    text = _source()

    forbidden_operations = (
        'op.alter_column("stock_lots"',
        'op.alter_column("stock_lot_consumptions"',
        'op.alter_column("inventory_cost_entries"',
        'op.drop_table("stock_lots"',
        'op.drop_table("stock_lot_consumptions"',
        'op.drop_table("inventory_cost_entries"',
    )

    for operation in forbidden_operations:
        assert operation not in text


def test_transfer_valuation_layer_migration_contract():
    text = _source()

    required = (
        '"warehouse_transfer_valuation_layers"',
        '"uq_wtl_valuation_parent_identity"',
        '"fk_wtvl_transfer_line_identity"',
        '"fk_wtvl_destination_receipt_line"',
        '"fk_wtvl_source_inventory_cost_entry"',
        '"fk_wtvl_source_fifo_consumption"',
        '"uq_wtvl_destination_receipt_line"',
        '"uq_wtvl_source_fifo_consumption"',
        '"ck_wtvl_quantity_positive"',
        '"ck_wtvl_unit_cost_nonnegative"',
        '"ck_wtvl_valuation_amount_nonnegative"',
    )

    for token in required:
        assert token in text


def test_business_transfer_line_migration_has_no_receipt_line():
    text = _source()

    business_start = text.index(
        '"warehouse_transfer_lines"'
    )

    valuation_start = text.index(
        '"warehouse_transfer_valuation_layers"'
    )

    business_block = text[
        business_start:valuation_start
    ]

    assert (
        '"receipt_document_line_id"'
        not in business_block
    )

    assert (
        '"uq_wtl_receipt_document_line"'
        not in business_block
    )

    assert (
        '"fk_wtl_receipt_document_line"'
        not in business_block
    )
