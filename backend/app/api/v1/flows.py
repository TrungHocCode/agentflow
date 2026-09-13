from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_workflow_service
from app.modules.workflows.schemas import WorkflowCreateRequest, WorkflowResponse
from app.modules.workflows.service import WorkflowService

router = APIRouter(prefix="/flows", tags=["Flows"])

@router.post("/", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_flow(
    flow_in: WorkflowCreateRequest,
    service: WorkflowService = Depends(get_workflow_service),
):
    return await service.create_workflow(flow_in)

@router.get("/", response_model=List[WorkflowResponse])
async def list_flows(
    service: WorkflowService = Depends(get_workflow_service),
):
    return await service.list_workflows()

@router.get("/{flow_id}", response_model=WorkflowResponse)
async def get_flow(
    flow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
):
    flow = await service.get_workflow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail=f"Flow with ID '{flow_id}' not found.")
    return flow
