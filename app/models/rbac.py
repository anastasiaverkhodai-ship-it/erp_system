from sqlalchemy import Column, ForeignKey, Table

from app.core.database import Base


user_roles = Table(
    "user_roles",
    Base.metadata,

    Column(
        "user_id",
        ForeignKey("users.id"),
        primary_key=True,
    ),

    Column(
        "role_id",
        ForeignKey("roles.id"),
        primary_key=True,
    ),
)


role_permissions = Table(
    "role_permissions",
    Base.metadata,

    Column(
        "role_id",
        ForeignKey("roles.id"),
        primary_key=True,
    ),

    Column(
        "permission_id",
        ForeignKey("permissions.id"),
        primary_key=True,
    ),
)