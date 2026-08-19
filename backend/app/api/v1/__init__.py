from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.api.v1.flows import router as flows_router
from app.api.v1.catalog import router as catalog_router
from app.api.v1.runs import router as runs_router

api_v1_router = APIRouter(prefix="/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(flows_router)
api_v1_router.include_router(catalog_router)
api_v1_router.include_router(runs_router)

