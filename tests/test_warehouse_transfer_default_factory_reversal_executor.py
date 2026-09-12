import ast
from pathlib import Path


EXECUTOR = Path(
    "app/services/"
    "warehouse_transfer_reconciliation_executor.py"
)


def _executor_function():
    tree = ast.parse(
        EXECUTOR.read_text()
    )

    return next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "execute_warehouse_transfer_reconciliation"
    )


def _awaited_calls():
    fn = _executor_function()

    calls = []

    for node in ast.walk(fn):
        if not isinstance(
            node,
            ast.Await,
        ):
            continue

        call = node.value

        if not isinstance(
            call,
            ast.Call,
        ):
            continue

        calls.append(
            (
                node.lineno,
                ast.unparse(
                    call.func
                ),
                ast.unparse(
                    call
                ),
            )
        )

    return sorted(
        calls,
        key=lambda item: item[0],
    )


def test_default_factory_is_created_before_action_execution():
    fn = _executor_function()

    source = ast.unparse(
        fn
    )

    assert (
        "if factory is None"
        in source
    )

    assert (
        "DefaultWarehouseTransferPhysicalFactory()"
        in source
    )


def test_reverse_uses_same_resolved_factory():
    fn = _executor_function()

    source = ast.unparse(
        fn
    )

    assert (
        "factory.reverse_transfer("
        in source
    )

    assert (
        "original_event=original"
        in source
    )

    assert (
        "reversal_date=plan.reversal_date"
        in source
    )

    assert (
        "created_by=created_by"
        in source
    )


def test_reverse_and_replace_physical_order():
    calls = _awaited_calls()

    reverse_calls = [
        item
        for item in calls
        if item[1]
        == "factory.reverse_transfer"
    ]

    create_calls = [
        item
        for item in calls
        if item[1]
        == "factory.create_transfer"
    ]

    assert len(
        reverse_calls
    ) == 1

    assert len(
        create_calls
    ) == 1

    assert (
        reverse_calls[0][0]
        < create_calls[0][0]
    )


def test_reversal_event_is_appended_after_physical_reverse():
    calls = _awaited_calls()

    reverse_line = next(
        lineno
        for lineno, name, call
        in calls
        if name
        == "factory.reverse_transfer"
    )

    event_lines = [
        lineno
        for lineno, name, call
        in calls
        if name
        == "append_warehouse_transfer_event"
    ]

    assert event_lines

    first_event_after_reverse = min(
        lineno
        for lineno
        in event_lines
        if lineno > reverse_line
    )

    assert (
        reverse_line
        < first_event_after_reverse
    )


def test_replacement_physical_create_precedes_replacement_event():
    calls = _awaited_calls()

    create_line = next(
        lineno
        for lineno, name, call
        in calls
        if name
        == "factory.create_transfer"
    )

    later_events = [
        lineno
        for lineno, name, call
        in calls
        if (
            name
            == "append_warehouse_transfer_event"
            and lineno
            > create_line
        )
    ]

    assert later_events

    assert (
        create_line
        < min(later_events)
    )


def test_no_transaction_ownership_or_generic_accounting():
    source = ast.unparse(
        _executor_function()
    )

    for forbidden in (
        ".commit(",
        ".rollback(",
        "post_document(",
        "reverse_journal_entry(",
        "create_default_reversal_engine(",
    ):
        assert (
            forbidden
            not in source
        )
