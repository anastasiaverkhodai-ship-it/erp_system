from app.services.register_catalog import SYSTEM_REGISTERS
from app.services.register_definition import RegisterDefinition


class RegisterCatalogError(Exception):
    """Base error for register catalog operations."""


class RegisterNotFoundError(RegisterCatalogError):
    """Raised when a register code is not registered."""


class DuplicateRegisterCodeError(RegisterCatalogError):
    """Raised when the same register code is registered twice."""


class RegisterCatalog:
    def __init__(
        self,
        registers: tuple[RegisterDefinition, ...],
    ) -> None:
        self._registers: dict[str, RegisterDefinition] = {}

        for register in registers:
            if register.code in self._registers:
                raise DuplicateRegisterCodeError(
                    f"Duplicate register code: '{register.code}'"
                )

            self._registers[register.code] = register

    def get(
        self,
        code: str,
    ) -> RegisterDefinition:
        register = self._registers.get(code)

        if register is None:
            raise RegisterNotFoundError(
                f"Register '{code}' is not registered"
            )

        return register

    def all(
        self,
    ) -> tuple[RegisterDefinition, ...]:
        return tuple(self._registers.values())


SYSTEM_REGISTER_CATALOG = RegisterCatalog(
    registers=SYSTEM_REGISTERS,
)