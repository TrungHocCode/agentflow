"""Conversation HTTP endpoints for the Build Phase."""

import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.api.dependencies import get_conversation_service, get_current_user_id
from app.modules.conversations.models import (
    ConversationCreateRequest,
    ConversationMessage,
    ConversationMessageRequest,
    ConversationResponse,
)
from app.modules.conversations.service import ConversationService


router = APIRouter(prefix="/conversations", tags=["Conversations"])


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    request: ConversationCreateRequest,
    service: ConversationService = Depends(get_conversation_service),
    user_id: str = Depends(get_current_user_id),
):
    return await service.create_conversation(
        workflow_id=request.workflow_id,
        title=request.title,
        metadata=request.metadata,
        user_id=user_id,
    )


@router.get("", response_model=List[ConversationResponse])
async def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    service: ConversationService = Depends(get_conversation_service),
    user_id: str = Depends(get_current_user_id),
):
    return await service.list_conversations(user_id=user_id, limit=limit)


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service),
    user_id: str = Depends(get_current_user_id),
):
    conversation = await service.get_conversation(conversation_id, user_id=user_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation with ID '{conversation_id}' not found.",
        )
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service),
    user_id: str = Depends(get_current_user_id),
):
    """Delete the owned conversation and its messages; linked runs are retained."""

    deleted = await service.delete_conversation(conversation_id, user_id=user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation with ID '{conversation_id}' not found.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=List[ConversationMessage])
async def list_conversation_messages(
    conversation_id: str,
    limit: int = Query(200, ge=1, le=1000),
    service: ConversationService = Depends(get_conversation_service),
    user_id: str = Depends(get_current_user_id),
):
    conversation = await service.get_conversation(conversation_id, user_id=user_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation with ID '{conversation_id}' not found.",
        )
    return await service.list_messages(conversation_id, limit=limit)


@router.post("/{conversation_id}/messages", response_model=None)
async def send_conversation_message(
    conversation_id: str,
    request: ConversationMessageRequest,
    service: ConversationService = Depends(get_conversation_service),
    user_id: str = Depends(get_current_user_id),
):
    if os.getenv("TESTING", "").lower() == "true":
        conversation = await service.send_message(
            conversation_id=conversation_id,
            content=request.content,
            user_id=user_id,
        )
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation with ID '{conversation_id}' not found.",
            )
        return conversation

    accepted = await service.start_message(
        conversation_id=conversation_id,
        content=request.content,
        user_id=user_id,
        turn_id=str(request.turn_id) if request.turn_id else None,
    )
    if accepted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation with ID '{conversation_id}' not found.",
        )
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=accepted)


@router.get("/{conversation_id}/events")
async def stream_conversation_events(
    conversation_id: str,
    turn_id: Optional[str] = Query(None),
    service: ConversationService = Depends(get_conversation_service),
    user_id: str = Depends(get_current_user_id),
):
    conversation = await service.get_conversation(conversation_id, user_id=user_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation with ID '{conversation_id}' not found.",
        )
    return StreamingResponse(
        service.stream_events(conversation_id, turn_id=turn_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
