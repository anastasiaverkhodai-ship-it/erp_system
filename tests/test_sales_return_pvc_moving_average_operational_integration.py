from pathlib import Path


def _text():
    return Path(
        "app/services/"
        "sales_return_operational_service.py"
    ).read_text()


def test_pvc_ma_runs_after_base_sales_return_cost_lifecycle():
    text = _text()

    base = text.find(
        "await reconcile_sales_return_"
        "cost_restoration_lifecycle_for_fulfillment_line("
    )

    pvc = text.find(
        "await reconcile_purchase_value_correction_"
        "moving_average_sales_return("
    )

    journals = text.find(
        "await post_created_purchase_value_correction_"
        "moving_average_sales_return_journals("
    )

    assert 0 <= base < pvc < journals


def test_pvc_ma_only_uses_wam_cost_events():
    text = _text()

    assert (
        "cost_event.valuation_method != "
        "'weighted_average_moving'"
        in text
    )


def test_reversal_uses_original_side_anchor():
    text = _text()

    assert (
        "cost_event.reversal_of_id"
        in text
    )

    assert (
        "if cost_event.reversal_of_id is not None"
        in text
    )

    assert (
        "else cost_event.id"
        in text
    )


def test_replacement_or_original_uses_self_as_anchor():
    text = _text()

    assert "else cost_event.id" in text


def test_only_one_final_anchor_is_kept_per_ice():
    text = _text()

    assert (
        "latest_wam_cost_anchor_by_ice = {}"
        in text
    )

    assert (
        "latest_wam_cost_anchor_by_ice["
        in text
    )

    assert (
        "cost_event.inventory_cost_entry_id"
        in text
    )


def test_return_date_is_forward_adjustment_date():
    text = _text()

    assert (
        "adjustment_date=event.return_date"
        in text
    )


def test_typed_pvc_journals_use_reconciliation_result():
    text = _text()

    assert (
        "post_created_purchase_value_correction_"
        "moving_average_sales_return_journals"
        in text
    )

    assert "result=pvc_ma_result" in text


def test_operational_transaction_is_caller_owned():
    text = _text()

    assert ".commit(" not in text
    assert ".rollback(" not in text
