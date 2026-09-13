from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_persisted_run_service, get_run_query_service
from app.modules.runs.models import (
    RunStartRequest,
    RunApproveRequest,
    RunChatRequest,
    RunResponse
)
from app.modules.runs.service import RunService

router = APIRouter(prefix="/runs", tags=["Runs"])


@router.post("/start", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
async def start_run(
    req: RunStartRequest,
    service: RunService = Depends(get_persisted_run_service),
):
    """
    Start a new execution run for a given flow.
    """
    run_doc = await service.create_run(
        flow_id=req.flow_id,
        input_message=req.input_message,
        metadata=req.metadata,
    )
    return run_doc


@router.get("/", response_model=List[RunResponse])
async def list_runs(
    flow_id: Optional[str] = Query(None, description="Filter runs by flow ID"),
    limit: int = Query(50, ge=1, le=200),
    service: RunService = Depends(get_run_query_service),
):
    """
    List run execution history.
    """
    return await service.list_runs(flow_id=flow_id, limit=limit)


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    service: RunService = Depends(get_run_query_service),
):
    """
    Get detailed information and status of a specific execution run.
    """
    run_doc = await service.get_run(run_id)
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found."
        )
    return run_doc


@router.post("/{run_id}/approve", response_model=RunResponse)
async def approve_run(
    run_id: str,
    req: RunApproveRequest,
    service: RunService = Depends(get_run_query_service),
):
    """
    Approve or reject a pending flow execution plan.
    """
    run_doc = await service.approve_run(
        run_id=run_id,
        approved=req.approved,
        feedback=req.feedback
    )
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found."
        )
    return run_doc


@router.post("/{run_id}/chat", response_model=RunResponse)
async def chat_run(
    run_id: str,
    req: RunChatRequest,
    service: RunService = Depends(get_run_query_service),
):
    """
    Send a follow-up message to a paused run's supervisor conversation.

    Dùng cho multi-turn conversation: user làm rõ yêu cầu với Supervisor
    trước khi approve plan. Graph được resume từ checkpoint với message mới
    và lại PAUSE chờ phản hồi tiếp.
    """
    run_doc = await service.send_message(
        run_id=run_id,
        message=req.message
    )
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found."
        )
    return run_doc


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: str,
    service: RunService = Depends(get_run_query_service),
):
    """
    Stream live execution logs and progress updates via Server-Sent Events (SSE).
    """
    run_doc = await service.get_run(run_id)
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found."
        )

    return StreamingResponse(
        service.stream_run_events(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
