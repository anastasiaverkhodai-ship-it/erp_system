from app.services.register_binding import RegisterBinding
from app.services.register_bindings import SYSTEM_REGISTER_BINDINGS
from app.services.register_catalog import SYSTEM_REGISTERS
from app.services.register_definition import RegisterDefinition


class RegisterIntegrityError(Exception):
    """Base error for register architecture integrity checks."""


class MissingRegisterBindingError(RegisterIntegrityError):
    """Raised when a system register has no persistence binding."""


class UnknownRegisterBindingError(RegisterIntegrityError):
    """Raised when a binding refers to an unknown system register."""


class RegisterDefinitionMismatchError(RegisterIntegrityError):
    """Raised when a binding uses a mismatched register definition."""


def validate_register_integrity(
    registers: tuple[RegisterDefinition, ...],
    bindings: tuple[RegisterBinding, ...],
) -> None:
    registers_by_code = {
        register.code: register
        for register in registers
    }

    bindings_by_code = {
        binding.code: binding
        for binding in bindings
    }

    missing_codes = sorted(
        set(registers_by_code)
        - set(bindings_by_code)
    )

    if missing_codes:
        raise MissingRegisterBindingError(
            "Missing register bindings: "
            + ", ".join(missing_codes)
        )

    unknown_codes = sorted(
        set(bindings_by_code)
        - set(registers_by_code)
    )

    if unknown_codes:
        raise UnknownRegisterBindingError(
            "Unknown register bindings: "
            + ", ".join(unknown_codes)
        )

    for code, binding in bindings_by_code.items():
        expected_definition = registers_by_code[code]

        if binding.definition != expected_definition:
            raise RegisterDefinitionMismatchError(
                f"Register definition mismatch for '{code}'"
            )


def validate_system_register_integrity() -> None:
    validate_register_integrity(
        registers=SYSTEM_REGISTERS,
        bindings=SYSTEM_REGISTER_BINDINGS,
    )