"""Validated user creation and public account representations."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

UserName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=50)]
Password = Annotated[str, Field(min_length=8, max_length=128)]


class UserCreateBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_name: UserName
    email: EmailStr = Field(max_length=255)
    password: Password

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class UserCreate(UserCreateBase):
    role: Literal["CASHIER", "ADMIN"]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_name: str
    email: str
    role: str
    created_at: datetime
