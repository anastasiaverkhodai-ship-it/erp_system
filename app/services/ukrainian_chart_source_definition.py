from dataclasses import dataclass


@dataclass(
    frozen=True,
    slots=True,
)
class UkrainianSyntheticAccountSource:
    """
    Official synthetic account code and name.

    ERP metadata such as account type,
    normal balance and posting behavior belongs
    to a separate metadata layer.
    """

    code: str
    name: str

    def __post_init__(self) -> None:
        if len(self.code) != 2:
            raise ValueError(
                "Synthetic account code must contain "
                "exactly 2 digits"
            )

        if not self.code.isdigit():
            raise ValueError(
                "Synthetic account code must be numeric"
            )

        if not self.name.strip():
            raise ValueError(
                "Synthetic account name cannot be empty"
            )

        if self.name != self.name.strip():
            raise ValueError(
                "Synthetic account name cannot contain "
                "leading or trailing whitespace"
            )
