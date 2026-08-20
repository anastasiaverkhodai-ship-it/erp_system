from enum import StrEnum


class UnitDimension(StrEnum):
    """
    Physical or logical dimension of a unit of measure.

    Units can only use general conversions when their
    dimensions are compatible.
    """

    COUNT = "count"
    MASS = "mass"
    VOLUME = "volume"
    LENGTH = "length"
    AREA = "area"
    TIME = "time"
    OTHER = "other"