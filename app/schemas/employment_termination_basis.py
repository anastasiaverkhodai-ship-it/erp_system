"""Structured legal evidence; free-text reasons never authorize deductions."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Article 22 of the Leave Act: exceptions to recovery of unearned annual leave.
EXEMPT_GROUNDS = {
    'military_service': '36.3',
    'transfer_or_elected_position': '36.5',
    'refusal_relocation_or_changed_terms': '36.6',
    'redundancy': '40.1',
    'incompatibility': '40.2',
    'long_incapacity': '40.5',
    'reinstatement_of_previous_employee': '40.6',
    'education': '38',
    'retirement': '38',
}


class EmploymentTerminationBasis(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ground: Literal['military_service','transfer_or_elected_position',
        'refusal_relocation_or_changed_terms','redundancy','incompatibility',
        'long_incapacity','reinstatement_of_previous_employee','education',
        'retirement','other']
    labour_code_provision: str = Field(min_length=1, max_length=100)
    source_reference: str = Field(min_length=1, max_length=1000)

    @field_validator('labour_code_provision','source_reference')
    @classmethod
    def nonempty(cls, value):
        value=value.strip()
        if not value:
            raise ValueError('Documented termination provision and evidence are required')
        return value

    @model_validator(mode='after')
    def provision_matches_ground(self):
        expected=EXEMPT_GROUNDS.get(self.ground)
        allowed = {expected}
        if self.ground in {'education','retirement'}:
            allowed.add('39')
        if expected is not None and self.labour_code_provision not in allowed:
            raise ValueError('Termination ground does not match the documented Labour Code provision')
        return self
