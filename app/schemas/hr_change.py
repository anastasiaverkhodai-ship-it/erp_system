from datetime import datetime
from pydantic import BaseModel, ConfigDict


class HRChangeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    entity_type: str
    entity_id: int
    before_state: dict | None
    after_state: dict
    changed_by: int | None
    changed_at: datetime
