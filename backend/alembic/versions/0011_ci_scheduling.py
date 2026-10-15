"""Add CI schedules and the in-app notification center."""

from alembic import op
import sqlalchemy as sa

revision = "0011_ci_scheduling"
down_revision = "0010_ci_comparison_promotion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("ci_schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("watchlist_id", sa.String(36), sa.ForeignKey("ci_watchlists.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("cadence", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("run_at", sa.String(5), nullable=False, server_default="08:00"),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("cadence IN ('daily','weekly')", name="ck_ci_schedule_cadence"))
    op.create_index("ix_ci_schedules_due", "ci_schedules", ["active", "next_due_at"])
    op.create_table("ci_notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("watchlist_id", sa.String(36), nullable=True),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False, server_default=""),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('run_completed','run_failed','brief_ready','schedule_skipped',"
                           "'schedule_blocked','schedule_fired')", name="ck_ci_notification_kind"))
    op.create_index("ix_ci_notifications_owner", "ci_notifications", ["owner_id", "read_at", "created_at"])


def downgrade() -> None:
    op.drop_table("ci_notifications")
    op.drop_table("ci_schedules")
