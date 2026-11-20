"""Store each account's own products so a request can reuse them."""

from alembic import op
import sqlalchemy as sa

revision = "0013_ci_my_products"
down_revision = "0012_agent_profile_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ci_my_products",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False, server_default=""),
        sa.Column("description_en", sa.String(1000), nullable=False, server_default=""),
        sa.Column("website", sa.String(2048), nullable=True),
        sa.Column("market", sa.String(16), nullable=False, server_default="global"),
        sa.Column("known_competitors", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('global','vn')", name="ck_ci_my_product_market"),
    )
    op.create_index("ix_ci_my_products_owner_id", "ci_my_products", ["owner_id"])
    op.create_index("uq_ci_my_products_owner_name", "ci_my_products", ["owner_id", sa.text("lower(name)")],
                    unique=True)
    op.create_index("uq_ci_my_products_owner_default", "ci_my_products", ["owner_id"], unique=True,
                    postgresql_where=sa.text("is_default"))
    op.add_column("ci_watchlists", sa.Column("my_product_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_ci_watchlists_my_product", "ci_watchlists", "ci_my_products", ["my_product_id"],
                          ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_ci_watchlists_my_product", "ci_watchlists", type_="foreignkey")
    op.drop_column("ci_watchlists", "my_product_id")
    op.drop_index("uq_ci_my_products_owner_default", table_name="ci_my_products")
    op.drop_index("uq_ci_my_products_owner_name", table_name="ci_my_products")
    op.drop_index("ix_ci_my_products_owner_id", table_name="ci_my_products")
    op.drop_table("ci_my_products")
