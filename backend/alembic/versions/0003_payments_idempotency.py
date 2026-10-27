"""Mock payments and idempotency keys.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id"), nullable=False),
        sa.Column("seat_ids", ARRAY(sa.Integer), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        # Set once the payment has been turned into a booking. Unique: one
        # payment can never pay for two bookings.
        sa.Column("booking_reference", UUID(as_uuid=True), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('charged', 'confirming', 'confirmed', 'refunded')", name="ck_payments_status"
        ),
    )
    op.create_table(
        "idempotency_keys",
        # Scoped per user and endpoint: two users may pick the same key.
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("endpoint", sa.String(64), primary_key=True),
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("response_status", sa.Integer),
        sa.Column("response_body", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("payments")
