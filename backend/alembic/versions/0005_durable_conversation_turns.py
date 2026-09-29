"""Persist conversation turn queue, lifecycle, and sequenced events.

Revision ID: 0005_durable_conversation_turns
Revises: 0004_workflow_contracts
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_durable_conversation_turns"
down_revision = "0004_workflow_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column(
            "user_message_id",
            sa.String(36),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assistant_message_id",
            sa.String(36),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("assistant_content", sa.Text(), nullable=False, server_default=""),
        sa.Column("plan", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_category", sa.String(64), nullable=True),
        sa.Column("error_id", sa.String(36), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("last_event_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_turns_status_created_at",
        "conversation_turns",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_conversation_turns_conversation_created",
        "conversation_turns",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_conversation_turns_conversation_status",
        "conversation_turns",
        ["conversation_id", "status"],
    )
    op.create_table(
        "conversation_turn_events",
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column(
            "turn_id",
            sa.String(36),
            sa.ForeignKey("conversation_turns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("turn_id", "sequence", name="uq_conversation_turn_event_sequence"),
    )
    op.create_index(
        "ix_conversation_turn_events_replay",
        "conversation_turn_events",
        ["conversation_id", "turn_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_turn_events_replay", table_name="conversation_turn_events")
    op.drop_table("conversation_turn_events")
    op.drop_index("ix_conversation_turns_conversation_status", table_name="conversation_turns")
    op.drop_index("ix_conversation_turns_conversation_created", table_name="conversation_turns")
    op.drop_index("ix_conversation_turns_status_created_at", table_name="conversation_turns")
    op.drop_table("conversation_turns")
