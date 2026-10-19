"""Documented Article 19 parental leave categories and calendar-year checks."""
from datetime import date
from calendar import monthrange
from itertools import combinations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SocialGround = Literal[
    'two_children_under_15', 'child_disability', 'adopted_child',
    'adult_disability_A_I', 'single_parent', 'guardian', 'foster_parent',
]


class SocialDependant(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_reference: str = Field(min_length=1, max_length=500)
    birth_date: date
    adult_disability_A_I: bool = False
    full_capacity_date: date | None = None

    @field_validator('document_reference')
    @classmethod
    def reference(cls, value):
        if not value.strip():
            raise ValueError('Dependant identity requires documentary evidence')
        return value.strip()


class SocialLeaveEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ground: SocialGround
    valid_from: date
    valid_to: date
    dependants: list[SocialDependant] = Field(min_length=1, max_length=30)
    eligibility_reference: str = Field(min_length=1, max_length=1000)
    other_parent_nonuse_reference: str | None = Field(default=None, max_length=1000)

    @field_validator('eligibility_reference', 'other_parent_nonuse_reference')
    @classmethod
    def reference(cls, value):
        if value is not None and not value.strip():
            raise ValueError('Social entitlement references cannot be blank')
        return value.strip() if value is not None else None

    @model_validator(mode='after')
    def evidence(self):
        if self.valid_from > self.valid_to:
            raise ValueError('Invalid social eligibility interval')
        if len({child.document_reference for child in self.dependants}) != len(self.dependants):
            raise ValueError('A dependant cannot be counted twice for the same ground')
        if any(child.birth_date > self.valid_to for child in self.dependants):
            raise ValueError('Dependant birth follows the documented eligibility')
        if self.ground in {'two_children_under_15', 'child_disability', 'adopted_child', 'foster_parent'}:
            if self.other_parent_nonuse_reference is None:
                raise ValueError('Document that the other parent has not used or been compensated for this yearly entitlement')
        return self


def validate_social_evidence(*, year, grounds, evidence, employment_start=None, employment_end=None):
    if not evidence or len({item.ground for item in evidence}) != len(evidence):
        raise ValueError('Document each distinct statutory social leave ground exactly once')
    if set(grounds) != {item.ground for item in evidence}:
        raise ValueError('Social grounds must match their documentary evidence')
    start = max(date(year, 1, 1), employment_start or date(year, 1, 1))
    end = min(date(year, 12, 31), employment_end or date(year, 12, 31))
    identities = {}
    for item in evidence:
        if max(start, item.valid_from) > min(end, item.valid_to):
            raise ValueError('Social eligibility does not overlap employment in the entitlement year')
        for child in item.dependants:
            previous = identities.setdefault(child.document_reference, child.birth_date)
            if previous != child.birth_date:
                raise ValueError('Conflicting birth dates for the same dependant')
            if child.birth_date > end:
                raise ValueError('Dependant was not born in the entitlement year')
        lower = max(start, item.valid_from)
        upper = min(end, item.valid_to)
        def birthday(child, years):
            year = child.birth_date.year + years
            return date(year, child.birth_date.month,
                        min(child.birth_date.day, monthrange(year, child.birth_date.month)[1]))
        def child_interval(child, years=18):
            # An acquired annual right remains usable after the age limit.
            # Establish it on a date when the documented conditions coexisted.
            limit = birthday(child, years)
            if child.full_capacity_date is not None:
                if child.full_capacity_date < child.birth_date:
                    raise ValueError('Civil capacity cannot predate birth')
                limit = min(limit, child.full_capacity_date)
            return max(lower, child.birth_date), min(upper, limit)
        if item.ground == 'two_children_under_15':
            intervals = [child_interval(child, 15) for child in item.dependants]
            if not any(max(a[0], b[0]) <= min(a[1], b[1]) for a,b in combinations(intervals,2)):
                raise ValueError('This social ground requires two children simultaneously within the statutory age limit')
        elif item.ground == 'adult_disability_A_I':
            if not any(child.adult_disability_A_I and
                       max(lower,birthday(child,18)) <= upper for child in item.dependants):
                raise ValueError('Document the adult dependant disability category A group I')
        else:
            eligible_child = any(a <= b for a,b in map(child_interval,item.dependants))
            eligible_adult = item.ground in {'single_parent','guardian'} and any(
                child.adult_disability_A_I and max(lower,birthday(child,18)) <= upper
                for child in item.dependants)
            if not eligible_child and not eligible_adult:
                raise ValueError('This social ground requires an eligible child or documented adult disability category')
