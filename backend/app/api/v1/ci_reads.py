"""Authenticated CI read surfaces: changes, briefs, snapshots and rounds. No SQL here."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.dependencies import (
    get_brief_service, get_current_user_id, get_investigation_service, get_snapshot_service,
)
from app.modules.competitive_intelligence.brief_contracts import BuildBriefRequest, IntelligenceBrief
from app.modules.competitive_intelligence.brief_service import BriefService
from app.modules.competitive_intelligence.investigation_contracts import InvestigationRound
from app.modules.competitive_intelligence.investigation_service import InvestigationService
from app.modules.competitive_intelligence.models import Page, Pagination
from app.modules.competitive_intelligence.snapshot_contracts import (
    ChangeCandidate, SourceSnapshot, SnapshotContent,
)
from app.modules.competitive_intelligence.snapshot_service import SnapshotService

router = APIRouter(tags=["Competitive Intelligence"])


@router.get("/watchlists/{watchlist_id}/changes", response_model=Page[ChangeCandidate])
async def list_changes(watchlist_id: UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                       service: SnapshotService = Depends(get_snapshot_service),
                       owner: str = Depends(get_current_user_id)) -> Page[ChangeCandidate]:
    items, total = await service.list_changes(str(watchlist_id), owner, limit, offset)
    return Page[ChangeCandidate](items=items, pagination=Pagination(limit=limit, offset=offset, total=total))


@router.get("/watchlists/{watchlist_id}/briefs", response_model=Page[IntelligenceBrief])
async def list_briefs(watchlist_id: UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                      service: BriefService = Depends(get_brief_service),
                      owner: str = Depends(get_current_user_id)) -> Page[IntelligenceBrief]:
    items, total = await service.list_briefs(str(watchlist_id), owner, limit, offset)
    return Page[IntelligenceBrief](items=items, pagination=Pagination(limit=limit, offset=offset, total=total))


@router.post("/watchlists/{watchlist_id}/briefs", response_model=IntelligenceBrief, status_code=201)
async def build_brief(watchlist_id: UUID, request: BuildBriefRequest,
                      service: BriefService = Depends(get_brief_service),
                      owner: str = Depends(get_current_user_id)) -> IntelligenceBrief:
    return await service.build_brief(watchlist_id, request.revision_id, request.run_id, owner,
                                     datetime.now(timezone.utc))


@router.get("/briefs/{brief_id}", response_model=IntelligenceBrief)
async def get_brief(brief_id: UUID, service: BriefService = Depends(get_brief_service),
                    owner: str = Depends(get_current_user_id)) -> IntelligenceBrief:
    return await service.get_brief(str(brief_id), owner)


@router.get("/briefs/{brief_id}/download")
async def download_brief(brief_id: UUID, service: BriefService = Depends(get_brief_service),
                         owner: str = Depends(get_current_user_id)) -> FileResponse:
    path, filename = await service.download_brief(str(brief_id), owner)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Brief file was not found.")
    return FileResponse(path, media_type="text/markdown", filename=filename)


@router.get("/sources/{source_id}/snapshots", response_model=list[SourceSnapshot])
async def list_snapshots(source_id: UUID, limit: int = Query(20, ge=1, le=50),
                         service: SnapshotService = Depends(get_snapshot_service),
                         owner: str = Depends(get_current_user_id)) -> list[SourceSnapshot]:
    return await service.list_snapshots(str(source_id), owner, limit)


@router.get("/snapshots/{snapshot_id}", response_model=SourceSnapshot)
async def get_snapshot(snapshot_id: UUID, service: SnapshotService = Depends(get_snapshot_service),
                       owner: str = Depends(get_current_user_id)) -> SourceSnapshot:
    return await service.get_snapshot(str(snapshot_id), owner)


@router.get("/snapshots/{snapshot_id}/content", response_model=SnapshotContent)
async def get_snapshot_content(snapshot_id: UUID, representation: str = Query("normalized"),
                               start: int = Query(0, ge=0), limit: int = Query(4000, ge=1, le=16000),
                               service: SnapshotService = Depends(get_snapshot_service),
                               owner: str = Depends(get_current_user_id)) -> SnapshotContent:
    return await service.read_content(str(snapshot_id), owner, representation, start, limit)


@router.get("/changes/{change_id}", response_model=ChangeCandidate)
async def get_change(change_id: UUID, service: SnapshotService = Depends(get_snapshot_service),
                     owner: str = Depends(get_current_user_id)) -> ChangeCandidate:
    return await service.get_change(str(change_id), owner)


@router.get("/runs/{run_id}/investigation-rounds", response_model=list[InvestigationRound])
async def list_rounds(run_id: UUID, service: InvestigationService = Depends(get_investigation_service),
                      owner: str = Depends(get_current_user_id)) -> list[InvestigationRound]:
    return await service.list_rounds(run_id, owner)
