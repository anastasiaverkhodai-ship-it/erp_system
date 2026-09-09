from pathlib import Path


def _service_text():
    return Path(
        "app/services/"
        "purchase_value_correction_moving_average_"
        "sales_return_reconciliation_service.py"
    ).read_text()


def test_sales_return_reconciliation_uses_fresh_baseline():
    text = _service_text()

    assert (
        "load_purchase_value_correction_"
        "moving_average_sales_return_baseline"
        in text
    )


def test_sales_return_reconciliation_uses_return_target_builder():
    text = _service_text()

    assert (
        "build_purchase_value_correction_"
        "moving_average_sales_return_targets"
        in text
    )


def test_sales_return_reconciliation_uses_existing_immutable_persistence():
    text = _service_text()

    assert (
        "reconcile_purchase_value_correction_"
        "moving_average_targets"
        in text
    )

    assert (
        "PurchaseValueCorrectionMovingAverageReplayTarget"
        in text
    )


def test_sales_return_reconciliation_uses_exact_source_product_warehouse():
    text = _service_text()

    assert "product_id=source.product_id" in text
    assert "warehouse_id=source.warehouse_id" in text


def test_sales_return_reconciliation_has_no_transaction_ownership():
    text = _service_text()

    assert ".commit(" not in text
    assert ".rollback(" not in text


def test_sales_return_reconciliation_does_not_mutate_base_ma_truth():
    text = _service_text()

    forbidden = (
        "MovingAverageBalance(",
        "MovingAverageMovement(",
        "InventoryCostEntry(",
    )

    for token in forbidden:
        assert token not in text
