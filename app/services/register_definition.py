from dataclasses import dataclass

from app.services.register_types import RegisterKind


@dataclass(frozen=True, slots=True)
class RegisterDefinition:
    """
    Immutable metadata describing an ERP register.

    code
        Stable internal system identifier.

    kind
        High-level register category.

    purpose
        Human-readable explanation of what the register stores
        or represents.
    """

    code: str
    kind: RegisterKind
    purpose: str