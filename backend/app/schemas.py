"""Request and response shapes."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=72)  # bcrypt ignores bytes after 72

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("invalid email")
        return value


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str


class EventOut(BaseModel):
    id: int
    name: str
    venue: str
    starts_at: datetime
    seats_total: int
    seats_booked: int


class SeatOut(BaseModel):
    id: int
    row_label: str
    number: int
    price: Decimal
    # free | held | booked, from the database plus Redis holds
    state: str
    mine: bool
    hold_expires_in_ms: int | None = None
