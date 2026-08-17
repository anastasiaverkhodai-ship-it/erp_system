from pydantic import BaseModel, EmailStr


class CompanyUserAdd(BaseModel):
    email: EmailStr
    role: str

class CompanyUserRoleUpdate(BaseModel):
    role: str

class CompanyUserResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    is_active: bool
    roles: list[str]

    