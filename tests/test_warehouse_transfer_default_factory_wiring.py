import ast
import inspect
from pathlib import Path

import app.services.warehouse_transfer_reconciliation_executor as executor


def test_executor_factory_is_optional():
    signature = inspect.signature(
        executor.execute_warehouse_transfer_reconciliation
    )

    matches = []

    for (
        name,
        parameter,
    ) in signature.parameters.items():
        annotation = str(
            parameter.annotation
        )

        if (
            "WarehouseTransferPhysicalFactory"
            in annotation
        ):
            matches.append(
                (
                    name,
                    parameter,
                )
            )

    assert len(matches) == 1

    _, parameter = matches[0]

    assert (
        parameter.default
        is None
    )


def test_executor_constructs_real_default_factory():
    path = Path(
        "app/services/"
        "warehouse_transfer_reconciliation_executor.py"
    )

    tree = ast.parse(
        path.read_text()
    )

    fn = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "execute_warehouse_transfer_reconciliation"
    )

    factory_parameter = None

    for arg in (
        fn.args.posonlyargs
        + fn.args.args
        + fn.args.kwonlyargs
    ):
        if arg.annotation is None:
            continue

        if (
            "WarehouseTransferPhysicalFactory"
            in ast.unparse(
                arg.annotation
            )
        ):
            factory_parameter = (
                arg.arg
            )
            break

    assert (
        factory_parameter
        is not None
    )

    source = ast.unparse(fn)

    assert (
        f"if {factory_parameter} is None"
        in source
    )

    assert (
        "DefaultWarehouseTransferPhysicalFactory()"
        in source
    )


def test_no_default_factory_created_at_import_time():
    text = Path(
        "app/services/"
        "warehouse_transfer_reconciliation_executor.py"
    ).read_text()

    tree = ast.parse(text)

    for node in tree.body:
        if isinstance(
            node,
            ast.Assign,
        ):
            value = ast.unparse(
                node.value
            )

            assert (
                "DefaultWarehouseTransferPhysicalFactory()"
                not in value
            )
