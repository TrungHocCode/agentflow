"""CI relational identities; immutable revision JSON owns historical configuration."""

from typing import Any

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WatchlistModel(Base):
    __tablename__ = "ci_watchlists"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    current_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    __table_args__ = (CheckConstraint("status IN ('active','archived')", name="ck_ci_watchlist_status"),
                     ForeignKeyConstraint(["id", "current_revision_id"],
                         ["ci_watchlist_revisions.watchlist_id", "ci_watchlist_revisions.id"],
                         name="fk_ci_current_revision", ondelete="RESTRICT", use_alter=True),
                     Index("ix_ci_watchlists_owner_status", "owner_id", "status", "updated_at"))


class WatchlistRevisionModel(Base):
    __tablename__ = "ci_watchlist_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watchlist_id: Mapped[str] = mapped_column(ForeignKey("ci_watchlists.id", ondelete="RESTRICT"))
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unapproved")
    approved_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    __table_args__ = (
        UniqueConstraint("watchlist_id", "revision_number", name="uq_ci_revision_number"),
        UniqueConstraint("watchlist_id", "id", name="uq_ci_revision_watchlist_id"),
        CheckConstraint("approval_status IN ('unapproved','approved')", name="ck_ci_revision_approval"))


class TrackedProductModel(Base):
    __tablename__ = "ci_products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watchlist_id: Mapped[str] = mapped_column(ForeignKey("ci_watchlists.id", ondelete="RESTRICT"))
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint("watchlist_id", "id", name="uq_ci_product_watchlist_id"),
                     CheckConstraint("kind IN ('own','competitor')", name="ck_ci_product_kind"))


class ProductProfileVersionModel(Base):
    __tablename__ = "ci_product_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("ci_products.id", ondelete="RESTRICT"))
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("product_id", "version_number", name="uq_ci_profile_number"),)


class TrackedSourceModel(Base):
    __tablename__ = "ci_sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("ci_products.id", ondelete="RESTRICT"))
    watchlist_id: Mapped[str] = mapped_column(ForeignKey("ci_watchlists.id", ondelete="RESTRICT"))
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (Index("ix_ci_sources_watchlist", "watchlist_id"),)
