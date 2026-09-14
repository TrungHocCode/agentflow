"""Add structured results, evidence and artifact metadata."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0003_research_outputs"
down_revision: Union[str, Sequence[str], None] = "0002_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind: sa.Connection, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "results"):
        op.create_table(
            "results",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("task_execution_id", sa.String(length=36), nullable=True),
            sa.Column("task_id", sa.String(length=64), nullable=True),
            sa.Column("result_type", sa.String(length=32), nullable=False),
            sa.Column("content", sa.JSON(), nullable=False),
            sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1"),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_results_run_created_at", "results", ["run_id", "created_at"])
    if not _table_exists(bind, "evidence"):
        op.create_table(
            "evidence",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("task_execution_id", sa.String(length=36), nullable=True),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("source_title", sa.String(length=500), nullable=True),
            sa.Column("source_type", sa.String(length=32), nullable=False, server_default="other"),
            sa.Column("excerpt", sa.Text(), nullable=True),
            sa.Column("content_hash", sa.String(length=128), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_evidence_run_collected_at", "evidence", ["run_id", "collected_at"])
    if not _table_exists(bind, "artifacts"):
        op.create_table(
            "artifacts",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("task_execution_id", sa.String(length=36), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=128), nullable=False),
            sa.Column("storage_uri", sa.Text(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("checksum", sa.String(length=128), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_artifacts_run_created_at", "artifacts", ["run_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    for index_name, table_name in (
        ("ix_artifacts_run_created_at", "artifacts"),
        ("ix_evidence_run_collected_at", "evidence"),
        ("ix_results_run_created_at", "results"),
    ):
        if _table_exists(bind, table_name):
            op.drop_index(index_name, table_name=table_name)
    for table_name in ("artifacts", "evidence", "results"):
        if _table_exists(bind, table_name):
            op.drop_table(table_name)
