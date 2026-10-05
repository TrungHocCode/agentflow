"""Authenticated CI commands/queries; routers never access SQL or execute agents."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from app.api.dependencies import get_current_user_id, get_intelligence_service
from app.modules.competitive_intelligence.models import (
    ApprovalRequest, CreateWatchlist, Page, Pagination, ProductProfileVersion, ProductView, Revision,
    SetupProposalRequest, SourceView,
    StartRunRequest, UpdateWatchlist, Watchlist,
)
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.runs.models import RunResponse

router = APIRouter(prefix="/watchlists", tags=["Competitive Intelligence"])


@router.post("/proposals", response_model=CreateWatchlist)
async def propose(request: SetupProposalRequest, service: IntelligenceService = Depends(get_intelligence_service),
                  owner: str = Depends(get_current_user_id)) -> CreateWatchlist:
    return await service.propose(str(request.conversation_id), owner)


@router.post("", response_model=Watchlist, status_code=201)
async def create(request: CreateWatchlist, service: IntelligenceService = Depends(get_intelligence_service),
                 owner: str = Depends(get_current_user_id)) -> Watchlist:
    return await service.create(request, owner)


@router.get("", response_model=Page[Watchlist])
async def list_watchlists(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                         service: IntelligenceService = Depends(get_intelligence_service),
                         owner: str = Depends(get_current_user_id)) -> Page[Watchlist]:
    return await service.list(owner, limit, offset)


@router.get("/{watchlist_id}", response_model=Watchlist)
async def get(watchlist_id: UUID, service: IntelligenceService = Depends(get_intelligence_service),
              owner: str = Depends(get_current_user_id)) -> Watchlist:
    return await service.get(str(watchlist_id), owner)


@router.patch("/{watchlist_id}", response_model=Watchlist)
async def update(watchlist_id: UUID, request: UpdateWatchlist,
                 service: IntelligenceService = Depends(get_intelligence_service),
                 owner: str = Depends(get_current_user_id)) -> Watchlist:
    return await service.update(str(watchlist_id), request, owner)


@router.delete("/{watchlist_id}", status_code=204)
async def archive(watchlist_id: UUID, service: IntelligenceService = Depends(get_intelligence_service),
                  owner: str = Depends(get_current_user_id)) -> Response:
    await service.archive(str(watchlist_id), owner)
    return Response(status_code=204)


@router.get("/{watchlist_id}/revisions", response_model=list[Revision])
async def revisions(watchlist_id: UUID, service: IntelligenceService = Depends(get_intelligence_service),
                    owner: str = Depends(get_current_user_id)) -> list[Revision]:
    return await service.revisions(str(watchlist_id), owner)


@router.post("/{watchlist_id}/revisions/{revision_id}/approval", response_model=Revision)
async def approve(watchlist_id: UUID, revision_id: UUID, request: ApprovalRequest,
                  service: IntelligenceService = Depends(get_intelligence_service),
                  owner: str = Depends(get_current_user_id)) -> Revision:
    return await service.approve(str(watchlist_id), str(revision_id), request.workflow_version_id, owner)


@router.get("/{watchlist_id}/products", response_model=Page[ProductView])
async def products(watchlist_id: UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                   service: IntelligenceService = Depends(get_intelligence_service),
                   owner: str = Depends(get_current_user_id)) -> Page[ProductView]:
    items = await service.products(str(watchlist_id), owner)
    return Page[ProductView](items=items[offset:offset + limit],
                            pagination=Pagination(limit=limit, offset=offset, total=len(items)))


@router.get("/{watchlist_id}/sources", response_model=Page[SourceView])
async def sources(watchlist_id: UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                  service: IntelligenceService = Depends(get_intelligence_service),
                  owner: str = Depends(get_current_user_id)) -> Page[SourceView]:
    items = await service.sources(str(watchlist_id), owner)
    return Page[SourceView](items=items[offset:offset + limit],
                           pagination=Pagination(limit=limit, offset=offset, total=len(items)))


@router.get("/{watchlist_id}/products/{product_id}/profiles", response_model=list[ProductProfileVersion])
async def profiles(watchlist_id: UUID, product_id: UUID,
                   service: IntelligenceService = Depends(get_intelligence_service),
                   owner: str = Depends(get_current_user_id)) -> list[ProductProfileVersion]:
    return await service.profiles(str(watchlist_id), str(product_id), owner)


@router.get("/{watchlist_id}/runs", response_model=Page[RunResponse])
async def runs(watchlist_id: UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
               service: IntelligenceService = Depends(get_intelligence_service),
               owner: str = Depends(get_current_user_id)) -> Page[RunResponse]:
    return await service.runs(str(watchlist_id), owner, limit, offset)


@router.post("/{watchlist_id}/runs", response_model=RunResponse, status_code=202)
async def start(watchlist_id: UUID, request: StartRunRequest,
                idempotency_key: str | None = Header(None, alias="Idempotency-Key", min_length=1, max_length=255),
                service: IntelligenceService = Depends(get_intelligence_service),
                owner: str = Depends(get_current_user_id)) -> RunResponse:
    document = await service.start(str(watchlist_id), request, owner, idempotency_key)
    return RunResponse(**document.model_dump())
