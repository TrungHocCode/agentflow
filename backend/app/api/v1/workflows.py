"""Canonical workflow endpoints.

The legacy ``/flows`` router remains available during migration. New clients
should use ``/workflows`` and the canonical definition adapter below.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user_id, get_workflow_service
from app.execution.state import FlowDefinition
from app.modules.workflows.contract import (
    canonicalize_workflow_definition,
    normalize_workflow_definition,
)
from app.modules.workflows.schemas import (
    WorkflowCreateRequest,
    WorkflowResponse,
    WorkflowUpdateRequest,
)
from app.modules.workflows.domain import WorkflowVersionRecord
from app.modules.workflows.service import WorkflowService
from app.shared.errors import ValidationError


router = APIRouter(prefix="/workflows", tags=["Workflows"])


class CanonicalWorkflowRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    definition: Optional[Dict[str, Any]] = None


class CanonicalWorkflowUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    definition: Optional[Dict[str, Any]] = None


class WorkflowValidationResponse(BaseModel):
    valid: bool
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)


class WorkflowVersionRequest(BaseModel):
    definition: Dict[str, Any]


def _legacy_create(request: CanonicalWorkflowRequest) -> WorkflowCreateRequest:
    definition = normalize_workflow_definition(request.definition)
    return WorkflowCreateRequest(
        name=request.name,
        description=request.description,
        definition=definition or None,
    )


def _legacy_update(request: CanonicalWorkflowUpdateRequest) -> WorkflowUpdateRequest:
    definition = normalize_workflow_definition(request.definition)
    return WorkflowUpdateRequest(
        name=request.name,
        description=request.description,
        definition=definition or None,
    )


@router.get("", response_model=List[WorkflowResponse])
async def list_workflows(
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    return await service.list_workflows(user_id=user_id)


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    request: CanonicalWorkflowRequest,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    return await service.create_workflow(_legacy_create(request), user_id=user_id)


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    workflow = await service.get_workflow(workflow_id, user_id=user_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"Workflow with ID '{workflow_id}' not found.")
    return workflow


@router.patch("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: str,
    request: CanonicalWorkflowUpdateRequest,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    workflow = await service.update_workflow(
        workflow_id,
        _legacy_update(request),
        user_id=user_id,
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"Workflow with ID '{workflow_id}' not found.")
    return workflow


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_workflow(
    workflow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    workflow = await service.archive_workflow(workflow_id, user_id=user_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"Workflow with ID '{workflow_id}' not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{workflow_id}/validate", response_model=WorkflowValidationResponse)
async def validate_workflow(
    workflow_id: str,
    request: CanonicalWorkflowUpdateRequest,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    if await service.get_workflow(workflow_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail=f"Workflow with ID '{workflow_id}' not found.")
    try:
        definition = normalize_workflow_definition(request.definition)
        from app.execution.state import FlowDefinition

        FlowDefinition.model_validate(definition)
        return WorkflowValidationResponse(valid=True)
    except ValidationError as exc:
        return WorkflowValidationResponse(
            valid=False,
            errors=[{"code": exc.code, "message": exc.message}],
        )
    except ValueError as exc:
        return WorkflowValidationResponse(
            valid=False,
            errors=[{"code": "invalid_definition", "message": str(exc)}],
        )


@router.get("/{workflow_id}/versions", response_model=List[WorkflowVersionRecord])
async def list_workflow_versions(
    workflow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    if await service.get_workflow(workflow_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail=f"Workflow with ID '{workflow_id}' not found.")
    return await service.list_versions(workflow_id, user_id=user_id)


@router.post("/{workflow_id}/versions", response_model=WorkflowVersionRecord, status_code=status.HTTP_201_CREATED)
async def create_workflow_version(
    workflow_id: str,
    request: WorkflowVersionRequest,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    definition = normalize_workflow_definition(request.definition)
    version = await service.create_version(
        workflow_id,
        FlowDefinition.model_validate(definition),
        user_id=user_id,
    )
    if version is None:
        raise HTTPException(status_code=404, detail=f"Workflow with ID '{workflow_id}' not found.")
    return version


@router.get("/{workflow_id}/versions/{version_id}", response_model=WorkflowVersionRecord)
async def get_workflow_version(
    workflow_id: str,
    version_id: str,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    version = await service.get_version(workflow_id, version_id, user_id=user_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"Workflow version '{version_id}' not found.")
    return version


@router.post(
    "/{workflow_id}/versions/{version_id}/publish",
    response_model=WorkflowVersionRecord,
)
async def publish_workflow_version(
    workflow_id: str,
    version_id: str,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    version = await service.publish_version(workflow_id, version_id, user_id=user_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"Workflow version '{version_id}' not found.")
    return version
@router.get("/{workflow_id}/definition", response_model=Dict[str, Any])
async def get_workflow_definition(
    workflow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
    user_id: str = Depends(get_current_user_id),
):
    workflow = await service.get_workflow(workflow_id, user_id=user_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"Workflow with ID '{workflow_id}' not found.")
    return canonicalize_workflow_definition(workflow.definition)
