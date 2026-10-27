"""Users, events, seats and bookings.

The unique constraint on bookings.seat_id is the database's last line of
defence against double booking.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("venue", sa.String(200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "seats",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id"), nullable=False, index=True),
        sa.Column("row_label", sa.String(1), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="available"),
        sa.Column("booked_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("event_id", "row_label", "number", name="uq_seats_event_row_number"),
        sa.CheckConstraint("status IN ('available', 'booked')", name="ck_seats_status"),
    )
    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("reference", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id"), nullable=False),
        sa.Column("seat_id", sa.Integer, sa.ForeignKey("seats.id"), nullable=False),
        sa.Column("strategy", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("seat_id", name="uq_bookings_seat_id"),
    )


def downgrade() -> None:
    op.drop_table("bookings")
    op.drop_table("seats")
    op.drop_table("events")
    op.drop_table("users")
