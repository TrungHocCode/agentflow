from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.postgres_client import get_db
from app.modules.flows.models import FlowModel, FlowCreate, FlowResponse

router = APIRouter(prefix="/flows", tags=["Flows"])

@router.post("/", response_model=FlowResponse, status_code=status.HTTP_201_CREATED)
async def create_flow(flow_in: FlowCreate, db: AsyncSession = Depends(get_db)):
    flow = FlowModel(
        name=flow_in.name,
        description=flow_in.description,
        definition=flow_in.definition.model_dump() if flow_in.definition else {}
    )
    db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return flow

@router.get("/", response_model=List[FlowResponse])
async def list_flows(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FlowModel).order_by(FlowModel.created_at.desc()))
    return result.scalars().all()

@router.get("/{flow_id}", response_model=FlowResponse)
async def get_flow(flow_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FlowModel).where(FlowModel.id == flow_id))
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(status_code=404, detail=f"Flow with ID '{flow_id}' not found.")
    return flow
