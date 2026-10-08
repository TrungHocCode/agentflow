"""Add CI intelligence briefs as immutable rendered records."""

from alembic import op
import sqlalchemy as sa

revision = "0009_ci_briefs"
down_revision = "0008_ci_investigation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("ci_briefs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("watchlist_id", sa.String(36), sa.ForeignKey("ci_watchlists.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("schema_version", sa.String(8), nullable=False, server_default="1"),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column("summary", sa.String(8000), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_coverage", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("conflicts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("limitations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("advisory_actions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("artifact_id", sa.String(200), nullable=False),
        sa.Column("artifact_uri", sa.String(512), nullable=False),
        sa.Column("artifact_hash", sa.String(64), nullable=False),
        sa.Column("observed_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("outcome IN ('baseline_created','no_change','changes_detected','partial',"
                           "'rebaseline_required','unavailable')", name="ck_ci_brief_outcome"),
        sa.CheckConstraint("quality IN ('complete','partial','insufficient')", name="ck_ci_brief_quality"))
    op.create_index("ix_ci_briefs_watchlist_created", "ci_briefs", ["watchlist_id", "created_at"])
    op.execute("""CREATE FUNCTION ci_guard_brief_history() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'CI brief history is retained'; END IF;
      IF NEW.id <> OLD.id OR NEW.watchlist_id <> OLD.watchlist_id OR NEW.revision_id <> OLD.revision_id
         OR NEW.run_id <> OLD.run_id OR NEW.owner_id <> OLD.owner_id OR NEW.outcome <> OLD.outcome
         OR NEW.summary IS DISTINCT FROM OLD.summary OR NEW.artifact_uri <> OLD.artifact_uri THEN
        RAISE EXCEPTION 'Recorded briefs are immutable';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER ci_briefs_immutable BEFORE UPDATE OR DELETE ON ci_briefs "
               "FOR EACH ROW EXECUTE FUNCTION ci_guard_brief_history()")


def downgrade() -> None:
    op.execute("DROP TRIGGER ci_briefs_immutable ON ci_briefs")
    op.execute("DROP FUNCTION ci_guard_brief_history()")
    op.drop_table("ci_briefs")
