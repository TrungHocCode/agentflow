from typing import List

from fastapi import APIRouter, Depends

from app.api.dependencies import get_catalog_service
from app.modules.catalog.domain import AgentDefinition, ToolDefinition
from app.modules.catalog.service import CatalogService

router = APIRouter(prefix="/catalog", tags=["Catalog"])
compat_router = APIRouter(tags=["Catalog"])

@router.get("/agents", response_model=List[AgentDefinition])
async def list_agents(
    service: CatalogService = Depends(get_catalog_service),
):
    return await service.list_agents()


@compat_router.get("/agents", response_model=List[AgentDefinition], include_in_schema=False)
async def list_agents_compat(
    service: CatalogService = Depends(get_catalog_service),
):
    return await service.list_agents()

@router.get("/tools", response_model=List[ToolDefinition])
async def list_tools(
    service: CatalogService = Depends(get_catalog_service),
):
    return await service.list_tools()


@compat_router.get("/tools", response_model=List[ToolDefinition], include_in_schema=False)
async def list_tools_compat(
    service: CatalogService = Depends(get_catalog_service),
):
    return await service.list_tools()
