"""Add user identity storage."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002_identity"
down_revision: Union[str, Sequence[str], None] = "0001_initial_persistence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "workflow_versions" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("workflow_versions")}
        if "input_schema" not in columns:
            op.add_column(
                "workflow_versions",
                sa.Column("input_schema", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            )
        if "output_schema" not in columns:
            op.add_column(
                "workflow_versions",
                sa.Column("output_schema", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            )
    if "users" in inspector.get_table_names():
        return
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("username", sa.String(length=120), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "workflow_versions" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("workflow_versions")}
        if "output_schema" in columns:
            op.drop_column("workflow_versions", "output_schema")
        if "input_schema" in columns:
            op.drop_column("workflow_versions", "input_schema")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
