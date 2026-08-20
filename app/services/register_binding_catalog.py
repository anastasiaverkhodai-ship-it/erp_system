from app.services.register_binding import RegisterBinding
from app.services.register_bindings import SYSTEM_REGISTER_BINDINGS


class RegisterBindingCatalogError(Exception):
    """Base error for register binding catalog operations."""


class RegisterBindingNotFoundError(
    RegisterBindingCatalogError
):
    """Raised when a register binding is not registered."""


class DuplicateRegisterBindingCodeError(
    RegisterBindingCatalogError
):
    """Raised when the same register code is bound twice."""


class RegisterBindingCatalog:
    def __init__(
        self,
        bindings: tuple[RegisterBinding, ...],
    ) -> None:
        self._bindings: dict[str, RegisterBinding] = {}

        for binding in bindings:
            if binding.code in self._bindings:
                raise DuplicateRegisterBindingCodeError(
                    f"Duplicate register binding code: "
                    f"'{binding.code}'"
                )

            self._bindings[binding.code] = binding

    def get(
        self,
        code: str,
    ) -> RegisterBinding:
        binding = self._bindings.get(code)

        if binding is None:
            raise RegisterBindingNotFoundError(
                f"Register binding '{code}' is not registered"
            )

        return binding

    def all(
        self,
    ) -> tuple[RegisterBinding, ...]:
        return tuple(self._bindings.values())


SYSTEM_REGISTER_BINDING_CATALOG = RegisterBindingCatalog(
    bindings=SYSTEM_REGISTER_BINDINGS,
)