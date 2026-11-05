"""Behavioral unit tests for the actual severance attribution guard."""

from types import SimpleNamespace

import pytest

from app.services.payroll_severance_calculated_financial import (
    require_severance_component_attribution,
)

PIT = "personal_income_tax"
LEVY = "military_levy"
USC = "unified_social_contribution"


def lines_for(names):
    return [
        SimpleNamespace(id=index, component=name)
        for index, name in enumerate(names, 1)
    ]


@pytest.mark.parametrize("component", [PIT, LEVY, USC])
def test_duplicate_component_rejected_without_source(component):
    rows = lines_for([PIT, LEVY, USC, component])

    with pytest.raises(
        ValueError,
        match="Severance statutory component has multiple lines",
    ):
        require_severance_component_attribution(
            rows, set()
        )


def test_single_line_components_preserved():
    rows = lines_for([PIT, LEVY, USC])
    result = require_severance_component_attribution(
        rows, set()
    )
    assert set(result) == {PIT, LEVY, USC}


def test_empty_input():
    assert require_severance_component_attribution(
        [], set()
    ) == {}


def test_reordered_input():
    rows = lines_for([USC, LEVY, PIT])
    result = require_severance_component_attribution(
        rows, set()
    )
    assert set(result) == {PIT, LEVY, USC}


def test_nonadjacent_duplicate_rejected():
    rows = lines_for([PIT, LEVY, USC, PIT])

    with pytest.raises(
        ValueError,
        match="multiple lines",
    ):
        require_severance_component_attribution(
            rows, set()
        )


def test_partial_attribution_does_not_clear_duplicate():
    rows = lines_for([PIT, LEVY, USC, PIT])

    with pytest.raises(
        ValueError,
        match="multiple lines",
    ):
        require_severance_component_attribution(
            rows, {1}
        )


def test_fully_attributed_dated_multiline_is_not_rejected_early():
    rows = lines_for([PIT, LEVY, USC, PIT])

    result = require_severance_component_attribution(
        rows, {1, 4}
    )
    assert len(result[PIT]) == 2


def test_unknown_component_rejected():
    rows = lines_for(["unsupported_component"])

    with pytest.raises(
        ValueError,
        match="Unsupported severance statutory component",
    ):
        require_severance_component_attribution(
            rows, set()
        )
