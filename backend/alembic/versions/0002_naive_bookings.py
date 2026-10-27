"""naive_bookings: the ledger for the deliberately broken demo endpoint.

It has the same shape as bookings but NO unique constraint on seat_id, so
the naive endpoint's race condition shows up as duplicate rows instead of
being caught by the database. Never use this table for real bookings.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "naive_bookings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("reference", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id"), nullable=False),
        sa.Column("seat_id", sa.Integer, sa.ForeignKey("seats.id"), nullable=False, index=True),
        sa.Column("strategy", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("naive_bookings")
