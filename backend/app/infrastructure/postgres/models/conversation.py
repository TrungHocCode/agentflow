"""SQLAlchemy models for durable build conversations."""

from typing import Any, Dict, Optional

from sqlalchemy import ForeignKey, JSON, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConversationModel(Base):
    """Conversation metadata and current workflow draft."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        default="default_user",
    )
    workflow_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("flows.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    draft_plan: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    conversation_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )

    __table_args__ = (Index("ix_conversations_user_id_updated_at", "user_id", "updated_at"),)


class ConversationMessageModel(Base):
    """One durable message belonging to a conversation."""

    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )

    __table_args__ = (
        Index(
            "ix_conversation_messages_conversation_id_created_at",
            "conversation_id",
            "created_at",
        ),
    )
