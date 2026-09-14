from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.api.dependencies import (
    get_current_user_id,
    get_persisted_run_service,
    get_run_query_service,
)
from app.modules.runs.models import (
    RunStartRequest,
    RunCreateRequest,
    RunApproveRequest,
    RunChatRequest,
    RunResponse
)
from app.modules.runs.service import RunService

router = APIRouter(prefix="/runs", tags=["Runs"])
workflow_run_router = APIRouter(prefix="/workflows", tags=["Runs"])


@router.post("/start", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
async def start_run(
    req: RunStartRequest,
    service: RunService = Depends(get_persisted_run_service),
    user_id: str = Depends(get_current_user_id),
):
    """
    Start a new execution run for a given flow.
    """
    run_doc = await service.create_run(
        flow_id=req.flow_id,
        input_message=req.input_message,
        metadata=req.metadata,
        user_id=user_id,
    )
    return run_doc


@router.get("/", response_model=List[RunResponse])
async def list_runs(
    flow_id: Optional[str] = Query(None, description="Filter runs by flow ID"),
    limit: int = Query(50, ge=1, le=200),
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    """
    List run execution history.
    """
    return await service.list_runs(flow_id=flow_id, limit=limit, user_id=user_id)


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    """
    Get detailed information and status of a specific execution run.
    """
    run_doc = await service.get_run(run_id, user_id=user_id)
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found."
        )
    return run_doc


@router.post("/{run_id}/approve", response_model=RunResponse)
@router.post("/{run_id}/approval", response_model=RunResponse)
async def approve_run(
    run_id: str,
    req: RunApproveRequest,
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    """
    Approve or reject a pending flow execution plan.
    """
    run_doc = await service.approve_run(
        run_id=run_id,
        approved=req.approved,
        feedback=req.feedback,
        user_id=user_id,
    )
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found."
        )
    return run_doc


@router.post("/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: str,
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    """Request cancellation of a queued or running run."""

    run_doc = await service.cancel_run(run_id, user_id=user_id)
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found.",
        )
    return run_doc


@router.post("/{run_id}/retry", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
async def retry_run(
    run_id: str,
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    """Retry failed work from the persisted workflow snapshot."""

    run_doc = await service.retry_run(run_id, user_id=user_id)
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed, interrupted or cancelled runs can be retried.",
        )
    return run_doc


@router.get("/{run_id}/events")
async def list_run_events(
    run_id: str,
    after_event_id: Optional[str] = Query(None, alias="after_event_id"),
    limit: int = Query(200, ge=1, le=1000),
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    """List persisted progress events for replay and dashboard loading."""

    run_doc = await service.get_run(run_id, user_id=user_id)
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found.",
        )
    return await service.list_run_events(
        run_id,
        after_event_id=after_event_id,
        limit=limit,
    )


async def _require_owned_run(service: RunService, run_id: str, user_id: str):
    run_doc = await service.get_run(run_id, user_id=user_id)
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found.",
        )
    return run_doc


@router.get("/{run_id}/results")
async def list_run_results(
    run_id: str,
    limit: int = Query(200, ge=1, le=1000),
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    """Return structured task results for the run after ownership validation."""

    await _require_owned_run(service, run_id, user_id)
    return await service.list_run_results(run_id, limit)


@router.get("/{run_id}/evidence")
async def list_run_evidence(
    run_id: str,
    limit: int = Query(200, ge=1, le=1000),
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    await _require_owned_run(service, run_id, user_id)
    return await service.list_run_evidence(run_id, limit)


@router.get("/{run_id}/evidence/{evidence_id}")
async def get_run_evidence(
    run_id: str,
    evidence_id: str,
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    await _require_owned_run(service, run_id, user_id)
    evidence = await service.get_run_evidence(run_id, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found.")
    return evidence


@router.get("/{run_id}/artifacts")
async def list_run_artifacts(
    run_id: str,
    limit: int = Query(200, ge=1, le=1000),
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    await _require_owned_run(service, run_id, user_id)
    artifacts = await service.list_run_artifacts(run_id, limit)
    return [
        {
            **artifact.model_dump(mode="json"),
            "download_url": f"/api/v1/runs/{run_id}/artifacts/{artifact.id}/download",
        }
        for artifact in artifacts
    ]


@router.get("/{run_id}/artifacts/{artifact_id}/download")
async def download_run_artifact(
    run_id: str,
    artifact_id: str,
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    await _require_owned_run(service, run_id, user_id)
    artifact = await service.get_run_artifact(run_id, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if service.artifact_storage is None:
        raise HTTPException(status_code=503, detail="Artifact storage is unavailable.")
    try:
        path = service.artifact_storage.resolve(artifact.storage_uri)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Artifact storage reference is invalid.") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file is no longer available.")
    return FileResponse(path, media_type=artifact.content_type, filename=artifact.name)


@workflow_run_router.post(
    "/{workflow_id}/runs",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow_run(
    workflow_id: str,
    req: RunCreateRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    service: RunService = Depends(get_persisted_run_service),
    user_id: str = Depends(get_current_user_id),
):
    """Create and enqueue an asynchronous run from a workflow snapshot."""

    run_doc = await service.create_workflow_run(
        workflow_id=workflow_id,
        input_data=req.input_data,
        execution_mode=req.execution_mode,
        metadata=req.metadata,
        idempotency_key=idempotency_key,
        user_id=user_id,
    )
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow with ID '{workflow_id}' not found.",
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
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    service: RunService = Depends(get_run_query_service),
    user_id: str = Depends(get_current_user_id),
):
    """
    Stream live execution logs and progress updates via Server-Sent Events (SSE).
    """
    run_doc = await service.get_run(run_id, user_id=user_id)
    if not run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found."
        )

    return StreamingResponse(
        service.stream_run_events(run_id, after_event_id=last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
