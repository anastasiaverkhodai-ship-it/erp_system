import inspect

import app.services.tax_recognition_lifecycle_service as tax_lifecycle


BRIDGE_CALL = (
    "reconcile_vat_advance_bridge_"
    "lifecycle_for_tax_calculation"
)

VAT_JOURNAL_CALL = (
    "_post_created_output_vat_recognition_events"
)


def test_bridge_runs_after_output_vat_journal_posting():
    source = inspect.getsource(
        tax_lifecycle._reconcile_ids
    )

    vat_journal_position = (
        source.index(
            VAT_JOURNAL_CALL
        )
    )

    bridge_position = (
        source.index(
            BRIDGE_CALL
        )
    )

    result_position = (
        source.index(
            "results.append"
        )
    )

    assert (
        vat_journal_position
        < bridge_position
        < result_position
    )


def test_bridge_receives_same_tax_calculation():
    source = inspect.getsource(
        tax_lifecycle._reconcile_ids
    )

    bridge_position = (
        source.index(
            BRIDGE_CALL
        )
    )

    bridge_source = (
        source[
            bridge_position:
            bridge_position + 700
        ]
    )

    assert (
        "tax_calculation_id"
        in bridge_source
    )

    assert (
        "calculation_id"
        in bridge_source
    )

    assert (
        "adjustment_date"
        in bridge_source
    )

    assert (
        "created_by"
        in bridge_source
    )


def test_bridge_error_is_wrapped_by_tax_lifecycle():
    source = inspect.getsource(
        tax_lifecycle._reconcile_ids
    )

    assert (
        "VatAdvanceBridgeLifecycleError"
        in source
    )

    assert (
        "VAT advance bridge lifecycle failed"
        in source
    )
