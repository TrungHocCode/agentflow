"""Store how the shared worker runs each agent profile, so a new agent is a catalog row."""

from alembic import op
import sqlalchemy as sa

revision = "0012_agent_profile_mode"
down_revision = "0011_ci_scheduling"
branch_labels = None
depends_on = None

# Built-in profiles whose behaviour used to be chosen by name in WorkerAgent.
MODES = {"source_researcher": "collect_evidence", "synthesis_agent": "reduce_evidence",
         "report_agent": "render_report"}
PRODUCT_CONTEXT = ("competitive_analyst", "product_analyst")


def upgrade() -> None:
    op.add_column("agent_catalog", sa.Column("mode", sa.String(32), nullable=False, server_default="tool_loop"))
    op.add_column("agent_catalog", sa.Column("reads_product_context", sa.Boolean(), nullable=False,
                                             server_default=sa.false()))
    catalog = sa.table("agent_catalog", sa.column("name", sa.String), sa.column("mode", sa.String),
                       sa.column("reads_product_context", sa.Boolean))
    for name, mode in MODES.items():
        op.execute(catalog.update().where(catalog.c.name == name).values(mode=mode))
    op.execute(catalog.update().where(catalog.c.name.in_(PRODUCT_CONTEXT)).values(reads_product_context=True))


def downgrade() -> None:
    op.drop_column("agent_catalog", "reads_product_context")
    op.drop_column("agent_catalog", "mode")
