"""Add CI configuration without rewriting historical core records."""

from alembic import op
import sqlalchemy as sa

revision = "0006_ci_configuration"
down_revision = "0005_durable_conversation_turns"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for name in ("created_at", "updated_at")]


def upgrade() -> None:
    op.create_table("ci_watchlists",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("current_revision_id", sa.String(36), nullable=True), *timestamps(),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_ci_watchlist_status"))
    op.create_index("ix_ci_watchlists_owner_status", "ci_watchlists", ["owner_id", "status", "updated_at"])
    op.create_table("ci_watchlist_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("watchlist_id", sa.String(36), sa.ForeignKey("ci_watchlists.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("approval_status", sa.String(16), nullable=False, server_default="unapproved"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(36), nullable=True), *timestamps(),
        sa.UniqueConstraint("watchlist_id", "revision_number", name="uq_ci_revision_number"),
        sa.UniqueConstraint("watchlist_id", "id", name="uq_ci_revision_watchlist_id"),
        sa.CheckConstraint("approval_status IN ('unapproved','approved')", name="ck_ci_revision_approval"))
    op.create_foreign_key("fk_ci_current_revision", "ci_watchlists", "ci_watchlist_revisions",
                          ["id", "current_revision_id"], ["watchlist_id", "id"], ondelete="RESTRICT")
    op.create_table("ci_products",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("watchlist_id", sa.String(36), sa.ForeignKey("ci_watchlists.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()), *timestamps(),
        sa.UniqueConstraint("watchlist_id", "id", name="uq_ci_product_watchlist_id"),
        sa.CheckConstraint("kind IN ('own','competitor')", name="ck_ci_product_kind"))
    op.create_table("ci_product_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("ci_products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("profile_hash", sa.String(64), nullable=False), *timestamps(),
        sa.UniqueConstraint("product_id", "version_number", name="uq_ci_profile_number"))
    op.create_table("ci_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("ci_products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("watchlist_id", sa.String(36), sa.ForeignKey("ci_watchlists.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config_version", sa.String(64), nullable=False), *timestamps())
    op.create_index("ix_ci_sources_watchlist", "ci_sources", ["watchlist_id"])
    op.add_column("runs", sa.Column("watchlist_id", sa.String(36), nullable=True))
    op.add_column("runs", sa.Column("watchlist_revision_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_runs_ci_watchlist", "runs", "ci_watchlists", ["watchlist_id"], ["id"],
                          ondelete="RESTRICT")
    op.create_foreign_key("fk_runs_ci_revision", "runs", "ci_watchlist_revisions",
                          ["watchlist_id", "watchlist_revision_id"], ["watchlist_id", "id"], ondelete="RESTRICT")
    op.create_index("uq_runs_ci_active_watchlist", "runs", ["watchlist_id"], unique=True,
        postgresql_where=sa.text("watchlist_id IS NOT NULL AND status IN "
                                 "('pending','created','waiting_for_approval','queued','running','paused')"))
    # Immutable content is also protected against accidental direct adapter SQL.
    op.execute("""CREATE FUNCTION ci_guard_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'CI history is retained'; END IF;
      IF TG_TABLE_NAME = 'ci_product_profiles' THEN
        RAISE EXCEPTION 'Product profiles are immutable';
      END IF;
      IF NEW.config::jsonb IS DISTINCT FROM OLD.config::jsonb OR NEW.config_hash <> OLD.config_hash
         OR NEW.id <> OLD.id OR NEW.watchlist_id <> OLD.watchlist_id
         OR NEW.revision_number <> OLD.revision_number OR NEW.created_at <> OLD.created_at
         OR (OLD.approval_status = 'approved' AND
             (NEW.approval_status <> OLD.approval_status OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
              OR NEW.approved_by IS DISTINCT FROM OLD.approved_by)) THEN
        RAISE EXCEPTION 'Revision content and recorded approval are immutable';
      END IF;
      RETURN NEW;
    END $$""")
    for table in ("ci_product_profiles", "ci_watchlist_revisions"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION ci_guard_immutable()")
    op.execute("""CREATE FUNCTION ci_guard_run_scope() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.watchlist_id IS NOT NULL AND
         (NEW.watchlist_id IS DISTINCT FROM OLD.watchlist_id
          OR NEW.watchlist_revision_id IS DISTINCT FROM OLD.watchlist_revision_id
          OR NEW.user_id <> OLD.user_id OR NEW.flow_id <> OLD.flow_id
          OR NEW.workflow_version_id IS DISTINCT FROM OLD.workflow_version_id
          OR NEW.input_data::jsonb IS DISTINCT FROM OLD.input_data::jsonb
          OR NEW.resolved_model_config::jsonb IS DISTINCT FROM OLD.resolved_model_config::jsonb) THEN
        RAISE EXCEPTION 'Accepted CI run scope is immutable';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER runs_ci_scope_immutable BEFORE UPDATE ON runs "
               "FOR EACH ROW EXECUTE FUNCTION ci_guard_run_scope()")


def downgrade() -> None:
    op.execute("DROP TRIGGER runs_ci_scope_immutable ON runs")
    op.execute("DROP FUNCTION ci_guard_run_scope()")
    op.drop_index("uq_runs_ci_active_watchlist", table_name="runs")
    op.drop_constraint("fk_runs_ci_revision", "runs", type_="foreignkey")
    op.drop_constraint("fk_runs_ci_watchlist", "runs", type_="foreignkey")
    op.drop_column("runs", "watchlist_revision_id")
    op.drop_column("runs", "watchlist_id")
    op.drop_constraint("fk_ci_current_revision", "ci_watchlists", type_="foreignkey")
    for table in ("ci_sources", "ci_product_profiles", "ci_products", "ci_watchlist_revisions", "ci_watchlists"):
        op.drop_table(table)
    op.execute("DROP FUNCTION ci_guard_immutable()")
