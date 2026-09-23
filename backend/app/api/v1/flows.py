from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_current_user_id, get_workflow_service
from app.modules.workflows.schemas import (
    WorkflowCreateRequest,
    WorkflowResponse,
    WorkflowUpdateRequest,
)
from app.modules.workflows.service import WorkflowService

router = APIRouter(prefix="/flows", tags=["Flows"])

@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_flow(
    flow_in: WorkflowCreateRequest,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    return await service.create_workflow(flow_in, user_id=user_id)

@router.get("", response_model=List[WorkflowResponse])
async def list_flows(
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    return await service.list_workflows(user_id=user_id)

@router.get("/{flow_id}", response_model=WorkflowResponse)
async def get_flow(
    flow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    flow = await service.get_workflow(flow_id, user_id=user_id)
    if flow is None:
        raise HTTPException(status_code=404, detail=f"Flow with ID '{flow_id}' not found.")
    return flow


@router.put("/{flow_id}", response_model=WorkflowResponse)
async def update_flow(
    flow_id: str,
    flow_in: WorkflowUpdateRequest,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    """Update workflow metadata and persist a new immutable version."""

    flow = await service.update_workflow(flow_id, flow_in, user_id=user_id)
    if flow is None:
        raise HTTPException(status_code=404, detail=f"Flow with ID '{flow_id}' not found.")
    return flow


@router.delete("/{flow_id}", response_model=WorkflowResponse)
async def archive_flow(
    flow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    """Archive a workflow while keeping versions and historical runs."""

    flow = await service.archive_workflow(flow_id, user_id=user_id)
    if flow is None:
        raise HTTPException(status_code=404, detail=f"Flow with ID '{flow_id}' not found.")
    return flow
