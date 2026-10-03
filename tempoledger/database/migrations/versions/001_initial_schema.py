"""Initial schema migration.

Revision ID: 001_initial
Revises:
Create Date: 2024-11-04 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create initial schema."""
    # Create scheduled_messages table
    op.create_table(
        "scheduled_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_content", sa.Text(), nullable=False),
        sa.Column("scheduled_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_send_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_status", sa.String(length=20), nullable=True),
        sa.Column("scheduling_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("delivery_metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )

    # Create indexes for scheduled_messages
    op.create_index(
        "idx_session_schedule",
        "scheduled_messages",
        ["session_id", "scheduled_time"],
    )
    op.create_index(
        "idx_send_status",
        "scheduled_messages",
        ["delivery_status", "actual_send_time"],
    )

    # Create scheduling_events table
    op.create_table(
        "scheduling_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("event_data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False
        ),
    )

    # Create indexes for scheduling_events
    op.create_index(
        "idx_session_events",
        "scheduling_events",
        ["session_id", "timestamp"],
    )
    op.create_index(
        "idx_event_type",
        "scheduling_events",
        ["event_type", "timestamp"],
    )


def downgrade() -> None:
    """Drop initial schema."""
    # Drop indexes
    op.drop_index("idx_event_type", table_name="scheduling_events")
    op.drop_index("idx_session_events", table_name="scheduling_events")
    op.drop_index("idx_send_status", table_name="scheduled_messages")
    op.drop_index("idx_session_schedule", table_name="scheduled_messages")

    # Drop tables
    op.drop_table("scheduling_events")
    op.drop_table("scheduled_messages")
