"""Add CI snapshot capture, baseline pointers and deterministic change candidates."""

from alembic import op
import sqlalchemy as sa

revision = "0007_ci_snapshots"
down_revision = "0006_ci_configuration"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for name in ("created_at", "updated_at")]


def upgrade() -> None:
    op.create_table("ci_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("ci_sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("fetch_outcome_id", sa.String(36), nullable=False),
        sa.Column("requested_url", sa.String(2048), nullable=False),
        sa.Column("final_url", sa.String(2048), nullable=False),
        sa.Column("source_context_hash", sa.String(64), nullable=False),
        sa.Column("source_config_version", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(32), nullable=False),
        sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("captured_uri", sa.String(512), nullable=False),
        sa.Column("captured_hash", sa.String(64), nullable=False),
        sa.Column("normalized_uri", sa.String(512), nullable=False),
        sa.Column("normalized_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("content_type", sa.String(64), nullable=True),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column("quality_reason_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), *timestamps(),
        sa.CheckConstraint("quality IN ('eligible','ineligible')", name="ck_ci_snapshot_quality"))
    op.create_index("ix_ci_snapshots_source_fetched", "ci_snapshots", ["source_id", "fetched_at"])
    op.create_index("ix_ci_snapshots_run_source", "ci_snapshots", ["run_id", "source_id"])
    op.create_table("ci_fetch_outcomes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("ci_sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("requested_url", sa.String(2048), nullable=False),
        sa.Column("final_url", sa.String(2048), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("fetch_status", sa.String(16), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("bytes_observed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("ci_snapshots.id", ondelete="RESTRICT"),
                  nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), *timestamps(),
        sa.CheckConstraint("fetch_status IN ('success','failed','blocked','empty')", name="ck_ci_outcome_status"))
    op.create_index("ix_ci_outcomes_run_source", "ci_fetch_outcomes", ["run_id", "source_id"])
    op.create_foreign_key("fk_ci_outcome_snapshot", "ci_fetch_outcomes", "ci_snapshots", ["snapshot_id"], ["id"],
                          ondelete="RESTRICT")
    op.create_foreign_key("fk_ci_snapshot_outcome", "ci_snapshots", "ci_fetch_outcomes", ["fetch_outcome_id"],
                          ["id"], ondelete="RESTRICT")
    op.create_table("ci_baselines",
        sa.Column("source_id", sa.String(36), sa.ForeignKey("ci_sources.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("comparison_context_hash", sa.String(64), primary_key=True),
        sa.Column("normalization_version", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("ci_snapshots.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=False), *timestamps())
    op.create_table("ci_run_comparisons",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("ci_sources.id", ondelete="RESTRICT"),
                  primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("baseline_snapshot_id", sa.String(36), nullable=True),
        sa.Column("current_snapshot_id", sa.String(36), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("promotion", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False), *timestamps(),
        sa.CheckConstraint("outcome IN ('baseline_created','no_change','changed','unavailable','rebaseline_required')",
                           name="ck_ci_comparison_outcome"),
        sa.CheckConstraint("quality IN ('complete','partial','insufficient')", name="ck_ci_comparison_quality"),
        sa.CheckConstraint("promotion IN ('pending','promoted','rejected_stale','skipped')",
                           name="ck_ci_comparison_promotion"))
    op.create_index("ix_ci_comparisons_run_source", "ci_run_comparisons", ["run_id", "source_id"])
    op.create_table("ci_change_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("ci_sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("before_snapshot_id", sa.String(36), sa.ForeignKey("ci_snapshots.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("after_snapshot_id", sa.String(36), sa.ForeignKey("ci_snapshots.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("section", sa.String(500), nullable=True),
        sa.Column("before_start", sa.Integer(), nullable=True),
        sa.Column("before_end", sa.Integer(), nullable=True),
        sa.Column("after_start", sa.Integer(), nullable=True),
        sa.Column("after_end", sa.Integer(), nullable=True),
        sa.Column("before_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("after_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("diff_algorithm_version", sa.String(32), nullable=False),
        sa.Column("diff_hash", sa.String(64), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False), *timestamps(),
        sa.CheckConstraint("kind IN ('added','removed','modified')", name="ck_ci_candidate_kind"),
        sa.UniqueConstraint("run_id", "source_id", "before_snapshot_id", "after_snapshot_id", "diff_hash",
                            name="uq_ci_candidate_dedup"))
    op.create_index("ix_ci_candidates_run_source", "ci_change_candidates", ["run_id", "source_id"])
    # Append-only history; baseline pointers advance by compare-and-swap in the repository only.
    # Outcomes allow a single NULL -> snapshot transition for out-of-order capture writes.
    op.execute("""CREATE FUNCTION ci_guard_snapshot_history() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'CI snapshot history is retained'; END IF;
      IF TG_TABLE_NAME = 'ci_baselines' THEN
        IF NEW.source_id <> OLD.source_id OR NEW.comparison_context_hash <> OLD.comparison_context_hash
           OR NEW.normalization_version <> OLD.normalization_version OR NEW.owner_id <> OLD.owner_id THEN
          RAISE EXCEPTION 'Baseline pointer identity is immutable';
        END IF;
        RETURN NEW;
      END IF;
      IF TG_TABLE_NAME = 'ci_fetch_outcomes' AND OLD.snapshot_id IS NULL AND NEW.snapshot_id IS NOT NULL
         AND NEW.id = OLD.id AND NEW.run_id = OLD.run_id AND NEW.source_id = OLD.source_id THEN
        RETURN NEW;
      END IF;
      RAISE EXCEPTION 'CI snapshot history is immutable';
    END $$""")
    for table in ("ci_fetch_outcomes", "ci_snapshots", "ci_baselines", "ci_run_comparisons",
                  "ci_change_candidates"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION ci_guard_snapshot_history()")


def downgrade() -> None:
    for table in ("ci_change_candidates", "ci_run_comparisons", "ci_baselines", "ci_fetch_outcomes",
                  "ci_snapshots"):
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
    op.execute("DROP FUNCTION ci_guard_snapshot_history()")
    op.drop_table("ci_change_candidates")
    op.drop_table("ci_run_comparisons")
    op.drop_table("ci_baselines")
    op.drop_constraint("fk_ci_snapshot_outcome", "ci_snapshots", type_="foreignkey")
    op.drop_constraint("fk_ci_outcome_snapshot", "ci_fetch_outcomes", type_="foreignkey")
    op.drop_table("ci_fetch_outcomes")
    op.drop_table("ci_snapshots")
