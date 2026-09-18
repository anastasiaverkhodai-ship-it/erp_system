"""
Effective-dated Ukrainian VAT declaration form catalog.

S11 boundary:
- selects the legal form contract by reporting period;
- does not sign or transmit declarations;
- KEP / submission / external acknowledgement belong to Phase 21.

The current J0200126 contract is intentionally marked as not XSD-verified
inside this repository until the exact official DPS XSD artifact is pinned
and independently verified.
"""

from dataclasses import dataclass
from datetime import date


class VatDeclarationFormCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class VatDeclarationFormDefinition:
    form_code: str
    form_version: int
    effective_from: date
    effective_until: date | None
    export_format: str
    official_xsd_verified: bool

    def applies_to(self, day: date) -> bool:
        if day < self.effective_from:
            return False
        if (
            self.effective_until is not None
            and day > self.effective_until
        ):
            return False
        return True


_FORM_DEFINITIONS = (
    VatDeclarationFormDefinition(
        form_code="J0200126",
        form_version=26,
        effective_from=date(2026, 9, 1),
        effective_until=None,
        export_format="xml",
        official_xsd_verified=False,
    ),
)


def resolve_vat_declaration_form(
    *,
    reporting_period_end: date,
) -> VatDeclarationFormDefinition:
    matches = [
        definition
        for definition in _FORM_DEFINITIONS
        if definition.applies_to(reporting_period_end)
    ]

    if len(matches) != 1:
        raise VatDeclarationFormCatalogError(
            "No unique VAT declaration form contract exists "
            f"for reporting period ending {reporting_period_end.isoformat()}"
        )

    return matches[0]
