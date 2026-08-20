from dataclasses import dataclass
from typing import Any

from app.services.register_definition import RegisterDefinition


@dataclass(frozen=True, slots=True)
class RegisterBinding:
    """
    Connects a logical ERP register definition
    to the persistence models that implement it.
    """

    definition: RegisterDefinition
    persistence_models: tuple[type[Any], ...]

    def __post_init__(self) -> None:
        if not self.persistence_models:
            raise ValueError(
                "Register binding must contain at least one "
                "persistence model"
            )

    @property
    def code(self) -> str:
        return self.definition.code