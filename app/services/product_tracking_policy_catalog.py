from app.services.product_tracking_policy_definition import (
    ProductTrackingPolicyDefinition,
)


class ProductTrackingPolicyCatalogError(Exception):
    """Base error for product tracking policy catalog operations."""


class ProductTrackingPolicyNotFoundError(
    ProductTrackingPolicyCatalogError
):
    """Raised when a product tracking policy cannot be found."""


class DuplicateProductTrackingPolicyError(
    ProductTrackingPolicyCatalogError
):
    """Raised when a product has more than one tracking policy."""


class ProductTrackingPolicyCatalog:
    def __init__(
        self,
        policies: tuple[
            ProductTrackingPolicyDefinition,
            ...,
        ],
    ) -> None:
        self._by_key: dict[
            tuple[int, int],
            ProductTrackingPolicyDefinition,
        ] = {}

        for policy in policies:
            key = (
                policy.company_id,
                policy.product_id,
            )

            if key in self._by_key:
                raise DuplicateProductTrackingPolicyError(
                    "Duplicate product tracking policy: "
                    f"company_id={policy.company_id}, "
                    f"product_id={policy.product_id}"
                )

            self._by_key[key] = policy

    def get(
        self,
        company_id: int,
        product_id: int,
    ) -> ProductTrackingPolicyDefinition:
        key = (
            company_id,
            product_id,
        )

        try:
            return self._by_key[key]
        except KeyError as exc:
            raise ProductTrackingPolicyNotFoundError(
                "Product tracking policy not found: "
                f"company_id={company_id}, "
                f"product_id={product_id}"
            ) from exc

    def for_company(
        self,
        company_id: int,
    ) -> tuple[
        ProductTrackingPolicyDefinition,
        ...,
    ]:
        return tuple(
            policy
            for policy in self._by_key.values()
            if policy.company_id == company_id
        )

    def all(
        self,
    ) -> tuple[
        ProductTrackingPolicyDefinition,
        ...,
    ]:
        return tuple(self._by_key.values())