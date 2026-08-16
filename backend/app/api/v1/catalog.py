from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.postgres_client import get_db
from app.modules.agent_catalog.models import AgentCatalogModel, ToolCatalogModel, AgentCatalogResponse, ToolCatalogResponse

router = APIRouter(prefix="/catalog", tags=["Catalog"])

@router.get("/agents", response_model=List[AgentCatalogResponse])
async def list_agents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AgentCatalogModel).where(AgentCatalogModel.is_active == True))
    return result.scalars().all()

@router.get("/tools", response_model=List[ToolCatalogResponse])
async def list_tools(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ToolCatalogModel).where(ToolCatalogModel.is_active == True))
    return result.scalars().all()
