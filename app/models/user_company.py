from sqlalchemy import Column, ForeignKey, Table

from app.core.database import Base


user_companies = Table(
    "user_companies",
    Base.metadata,

    Column(
        "user_id",
        ForeignKey("users.id"),
        primary_key=True,
    ),

    Column(
        "company_id",
        ForeignKey("companies.id"),
        primary_key=True,
    ),
)