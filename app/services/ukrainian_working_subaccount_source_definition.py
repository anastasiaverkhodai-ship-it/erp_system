from dataclasses import dataclass


@dataclass(
    frozen=True,
    slots=True,
)
class UkrainianWorkingSubaccountSource:
    code: str
    name: str
    parent_code: str

    def __post_init__(self) -> None:
        if (
            len(self.code) != 3
            or not self.code.isdigit()
        ):
            raise ValueError(
                "Working subaccount code must contain "
                "exactly 3 digits"
            )

        if (
            len(self.parent_code) != 2
            or not self.parent_code.isdigit()
        ):
            raise ValueError(
                "Working subaccount parent code must "
                "contain exactly 2 digits"
            )

        if self.code[:2] != self.parent_code:
            raise ValueError(
                "Working subaccount must belong to "
                "its parent synthetic account"
            )

        if not self.name:
            raise ValueError(
                "Working subaccount name cannot be empty"
            )

        if self.name != self.name.strip():
            raise ValueError(
                "Working subaccount name must be stripped"
            )
