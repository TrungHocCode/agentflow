"""Allow the single pending promotion decision on recorded run comparisons."""

from alembic import op

revision = "0010_ci_comparison_promotion"
down_revision = "0009_ci_briefs"
branch_labels = None
depends_on = None

FORWARD = """CREATE OR REPLACE FUNCTION ci_guard_snapshot_history() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'CI snapshot history is retained'; END IF;
      IF TG_TABLE_NAME = 'ci_baselines' THEN
        IF NEW.source_id <> OLD.source_id OR NEW.comparison_context_hash <> OLD.comparison_context_hash
           OR NEW.normalization_version <> OLD.normalization_version OR NEW.owner_id <> OLD.owner_id THEN
          RAISE EXCEPTION 'Baseline pointer identity is immutable';
        END IF;
        RETURN NEW;
      END IF;
      IF TG_TABLE_NAME = 'ci_fetch_outcomes' THEN
        IF OLD.snapshot_id IS NULL AND NEW.snapshot_id IS NOT NULL
           AND NEW.id = OLD.id AND NEW.run_id = OLD.run_id AND NEW.source_id = OLD.source_id THEN
          RETURN NEW;
        END IF;
        RAISE EXCEPTION 'CI snapshot history is immutable';
      END IF;
      IF TG_TABLE_NAME = 'ci_run_comparisons' THEN
        IF OLD.promotion = 'pending' AND NEW.promotion IN ('promoted','skipped','rejected_stale')
           AND NEW.run_id = OLD.run_id AND NEW.source_id = OLD.source_id AND NEW.owner_id = OLD.owner_id
           AND NEW.outcome = OLD.outcome AND NEW.quality = OLD.quality
           AND NEW.baseline_snapshot_id IS NOT DISTINCT FROM OLD.baseline_snapshot_id
           AND NEW.current_snapshot_id IS NOT DISTINCT FROM OLD.current_snapshot_id
           AND NEW.decided_at = OLD.decided_at THEN
          RETURN NEW;
        END IF;
        RAISE EXCEPTION 'CI snapshot history is immutable';
      END IF;
      RAISE EXCEPTION 'CI snapshot history is immutable';
    END $$"""

BACKWARD = """CREATE OR REPLACE FUNCTION ci_guard_snapshot_history() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'CI snapshot history is retained'; END IF;
      IF TG_TABLE_NAME = 'ci_baselines' THEN
        IF NEW.source_id <> OLD.source_id OR NEW.comparison_context_hash <> OLD.comparison_context_hash
           OR NEW.normalization_version <> OLD.normalization_version OR NEW.owner_id <> OLD.owner_id THEN
          RAISE EXCEPTION 'Baseline pointer identity is immutable';
        END IF;
        RETURN NEW;
      END IF;
      IF TG_TABLE_NAME = 'ci_fetch_outcomes' THEN
        IF OLD.snapshot_id IS NULL AND NEW.snapshot_id IS NOT NULL
           AND NEW.id = OLD.id AND NEW.run_id = OLD.run_id AND NEW.source_id = OLD.source_id THEN
          RETURN NEW;
        END IF;
        RAISE EXCEPTION 'CI snapshot history is immutable';
      END IF;
      RAISE EXCEPTION 'CI snapshot history is immutable';
    END $$"""


def upgrade() -> None:
    op.execute(FORWARD)


def downgrade() -> None:
    op.execute(BACKWARD)
