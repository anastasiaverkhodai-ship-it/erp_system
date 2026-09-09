from pathlib import Path


def _text():
    return Path(
        "app/services/"
        "purchase_value_correction_moving_average_"
        "lifecycle_service.py"
    ).read_text()


def test_sales_return_journal_wrapper_exists():
    text = _text()

    assert (
        "post_created_purchase_value_correction_"
        "moving_average_sales_return_journals"
        in text
    )


def test_sales_return_journal_wrapper_uses_existing_event_consumer():
    text = _text()

    assert (
        "created_events=result.created_events"
        in text
    )

    assert (
        "return await _post_created_events("
        in text
    )


def test_sales_return_journal_wrapper_is_typed():
    text = _text()

    assert (
        "PurchaseValueCorrectionMovingAverage"
        "SalesReturnReconciliationResult"
        in text
    )


def test_sales_return_lifecycle_keeps_transaction_with_caller():
    text = _text()

    assert ".commit(" not in text
    assert ".rollback(" not in text
