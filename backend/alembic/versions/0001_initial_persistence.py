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


def _upgrade_existing_phase_two_schema() -> None:
    """Prepare a database created by the pre-Alembic prototype.

    The prototype created ``flows``, ``agent_catalog`` and ``tool_catalog``
    directly with SQLAlchemy.  The first version of this migration treated
    the presence of ``flows`` as proof that the whole schema existed and
    returned early.  That left later migrations with missing foreign-key
    targets such as ``runs``.

    Keep the legacy data and only apply the small compatibility change that
    belongs to this baseline migration.  The main upgrade below creates every
    missing table individually.
    """

    if context.is_offline_mode():
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "flows" not in inspector.get_table_names():
        return
    flow_columns = {column["name"] for column in inspector.get_columns("flows")}
    if "status" not in flow_columns:
        op.add_column(
            "flows",
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        )
        op.alter_column("flows", "status", server_default=None)


def upgrade() -> None:
    _upgrade_existing_phase_two_schema()

    if context.is_offline_mode():
        existing_tables: set[str] = set()
        existing_indexes: set[str] = set()
    else:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        existing_tables = set(inspector.get_table_names())
        existing_indexes = {
            index["name"]
            for table_name in existing_tables
            for index in inspector.get_indexes(table_name)
        }

    if "flows" not in existing_tables:
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
        existing_tables.add("flows")

    if "agent_catalog" not in existing_tables:
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
        existing_tables.add("agent_catalog")

    if "tool_catalog" not in existing_tables:
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
        existing_tables.add("tool_catalog")

    if "conversations" not in existing_tables:
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
        existing_tables.add("conversations")

    if "workflow_versions" not in existing_tables:
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
        existing_tables.add("workflow_versions")

    if "conversation_messages" not in existing_tables:
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
        existing_tables.add("conversation_messages")

    if "runs" not in existing_tables:
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
        existing_tables.add("runs")

    if "run_events" not in existing_tables:
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
        existing_tables.add("run_events")

    indexes = (
        (
            "ix_conversations_user_id_updated_at",
            "conversations",
            ["user_id", "updated_at"],
        ),
        (
            "ix_conversation_messages_conversation_id_created_at",
            "conversation_messages",
            ["conversation_id", "created_at"],
        ),
        (
            "ix_workflow_versions_workflow_number",
            "workflow_versions",
            ["workflow_id", "version_number"],
        ),
        ("ix_runs_user_id_created_at", "runs", ["user_id", "created_at"]),
        ("ix_runs_flow_id_created_at", "runs", ["flow_id", "created_at"]),
        ("ix_runs_status", "runs", ["status"]),
        ("ix_run_events_run_sequence", "run_events", ["run_id", "sequence"]),
    )
    for index_name, table_name, columns in indexes:
        if index_name not in existing_indexes:
            op.create_index(index_name, table_name, columns)
            existing_indexes.add(index_name)


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
