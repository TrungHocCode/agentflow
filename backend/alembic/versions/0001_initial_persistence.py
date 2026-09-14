"""Create the initial AgentFlow persistence schema.

Revision ID: 0001_initial_persistence
Revises:
Create Date: 2026-09-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


revision: str = "0001_initial_persistence"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def _upgrade_existing_phase_two_schema() -> bool:
    """Baseline a database created by the pre-Alembic prototype."""

    if context.is_offline_mode():
        return False
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "flows" not in inspector.get_table_names():
        return False
    flow_columns = {column["name"] for column in inspector.get_columns("flows")}
    if "status" not in flow_columns:
        op.add_column(
            "flows",
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        )
        op.alter_column("flows", "status", server_default=None)
    return True


def upgrade() -> None:
    if _upgrade_existing_phase_two_schema():
        return

    op.create_table(
        "flows",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("definition", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "agent_catalog",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("tool_names", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "tool_catalog",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("config_schema", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "workflow_id",
            sa.String(length=36),
            sa.ForeignKey("flows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("draft_plan", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column(
            "workflow_id",
            sa.String(length=36),
            sa.ForeignKey("flows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="published"),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id",
            "version_number",
            name="uq_workflow_versions_workflow_number",
        ),
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column(
            "flow_id",
            sa.String(length=36),
            sa.ForeignKey("flows.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workflow_version_id",
            sa.String(length=128),
            sa.ForeignKey("workflow_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="created"),
        sa.Column("approval_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("execution_mode", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="executing"),
        sa.Column("current_task", sa.JSON(), nullable=True),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("logs", sa.JSON(), nullable=False),
        sa.Column("result_storage", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("input_data", sa.JSON(), nullable=False),
        sa.Column("resolved_model_config", sa.JSON(), nullable=False),
        sa.Column("checkpoint_ref", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_time_ms", sa.Float(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("idempotency_key"),
    )

    op.create_table(
        "run_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1"),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("phase", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
    )

    op.create_index(
        "ix_conversations_user_id_updated_at",
        "conversations",
        ["user_id", "updated_at"],
    )
    op.create_index(
        "ix_conversation_messages_conversation_id_created_at",
        "conversation_messages",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_workflow_versions_workflow_number",
        "workflow_versions",
        ["workflow_id", "version_number"],
    )
    op.create_index(
        "ix_runs_user_id_created_at",
        "runs",
        ["user_id", "created_at"],
    )
    op.create_index("ix_runs_flow_id_created_at", "runs", ["flow_id", "created_at"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index(
        "ix_run_events_run_sequence",
        "run_events",
        ["run_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_events_run_sequence", table_name="run_events")
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_flow_id_created_at", table_name="runs")
    op.drop_index("ix_runs_user_id_created_at", table_name="runs")
    op.drop_index(
        "ix_workflow_versions_workflow_number",
        table_name="workflow_versions",
    )
    op.drop_index(
        "ix_conversation_messages_conversation_id_created_at",
        table_name="conversation_messages",
    )
    op.drop_index("ix_conversations_user_id_updated_at", table_name="conversations")
    op.drop_table("run_events")
    op.drop_table("runs")
    op.drop_table("conversation_messages")
    op.drop_table("workflow_versions")
    op.drop_table("conversations")
    op.drop_table("tool_catalog")
    op.drop_table("agent_catalog")
    op.drop_table("flows")
