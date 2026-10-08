"""Add CI investigation rounds as immutable accepted DAG deltas."""

from alembic import op
import sqlalchemy as sa

revision = "0008_ci_investigation"
down_revision = "0007_ci_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("ci_investigation_rounds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("watchlist_id", sa.String(36), sa.ForeignKey("ci_watchlists.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("parent_round_id", sa.String(36), nullable=True),
        sa.Column("tasks", sa.JSON(), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("reserved_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("rejection_reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('proposed','accepted','rejected','superseded','completed','interrupted')",
                           name="ck_ci_round_status"),
        sa.UniqueConstraint("run_id", "round_number", name="uq_ci_round_run_number"))
    op.create_index("ix_ci_rounds_run", "ci_investigation_rounds", ["run_id", "round_number"])
    # Accepted rounds are immutable DAG deltas; only status/decision columns may advance
    # along proposed -> accepted/rejected -> completed/interrupted/superseded.
    op.execute("""CREATE FUNCTION ci_guard_round_history() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'CI investigation history is retained'; END IF;
      IF NEW.id <> OLD.id OR NEW.run_id <> OLD.run_id OR NEW.watchlist_id <> OLD.watchlist_id
         OR NEW.revision_id <> OLD.revision_id OR NEW.owner_id <> OLD.owner_id
         OR NEW.round_number <> OLD.round_number OR NEW.tasks::jsonb IS DISTINCT FROM OLD.tasks::jsonb
         OR NEW.scope_digest <> OLD.scope_digest OR NEW.reserved_calls <> OLD.reserved_calls
         OR NEW.reserved_tokens <> OLD.reserved_tokens OR NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION 'Accepted investigation rounds are immutable';
      END IF;
      IF NOT ((OLD.status = 'proposed' AND NEW.status IN ('accepted','rejected'))
              OR (OLD.status = 'accepted' AND NEW.status IN ('completed','interrupted','superseded'))) THEN
        RAISE EXCEPTION 'Invalid investigation round transition';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER ci_investigation_rounds_immutable BEFORE UPDATE OR DELETE "
               "ON ci_investigation_rounds FOR EACH ROW EXECUTE FUNCTION ci_guard_round_history()")


def downgrade() -> None:
    op.execute("DROP TRIGGER ci_investigation_rounds_immutable ON ci_investigation_rounds")
    op.execute("DROP FUNCTION ci_guard_round_history()")
    op.drop_table("ci_investigation_rounds")
