"""Admin raffle campaign management for cabinet."""

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud import raffle as raffle_crud
from app.database.models import RaffleCampaignStatus, RafflePrizeType, User
from app.services.raffle.service import draw_winners

from ..dependencies import get_cabinet_db, require_permission


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/raffle', tags=['Cabinet Admin Raffle'])


class AdminRaffleCampaignItem(BaseModel):
    id: int
    name: str
    description: str | None = None
    status: str
    starts_at: datetime
    ends_at: datetime | None = None
    max_winners: int
    prize_type: str
    prize_value: int | None = None
    prize_text: str | None = None
    tickets: int = 0
    unique_users: int = 0
    winners: int = 0
    created_at: datetime | None = None


class AdminRaffleCampaignListResponse(BaseModel):
    enabled: bool
    campaigns: list[AdminRaffleCampaignItem]


class CreateRaffleCampaignRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    max_winners: int = Field(1, ge=1, le=1000)
    prize_type: str = Field(RafflePrizeType.CUSTOM.value)
    prize_value: int | None = None
    prize_text: str | None = None
    ends_at: datetime | None = None


class AdminRaffleWinnerItem(BaseModel):
    id: int
    user_id: int
    ticket_code: str | None = None
    place: int
    prize_type: str | None = None
    prize_value: int | None = None
    prize_text: str | None = None
    awarded: bool


class AdminRaffleDrawResponse(BaseModel):
    campaign_id: int
    status: str
    winners: list[AdminRaffleWinnerItem]


def _campaign_item(campaign, stats: dict[str, int]) -> AdminRaffleCampaignItem:
    return AdminRaffleCampaignItem(
        id=campaign.id,
        name=campaign.name,
        description=campaign.description,
        status=campaign.status,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        max_winners=campaign.max_winners,
        prize_type=campaign.prize_type,
        prize_value=campaign.prize_value,
        prize_text=campaign.prize_text,
        tickets=stats.get('tickets', 0),
        unique_users=stats.get('unique_users', 0),
        winners=stats.get('winners', 0),
        created_at=campaign.created_at,
    )


@router.get('/campaigns', response_model=AdminRaffleCampaignListResponse)
async def list_raffle_campaigns(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(require_permission('raffle:read')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """List raffle campaigns with ticket counts."""
    campaigns = await raffle_crud.list_campaigns(db, limit=limit, offset=offset)
    items: list[AdminRaffleCampaignItem] = []
    for campaign in campaigns:
        stats = await raffle_crud.get_campaign_ticket_stats(db, campaign.id)
        items.append(_campaign_item(campaign, stats))
    return AdminRaffleCampaignListResponse(enabled=settings.is_raffle_enabled(), campaigns=items)


@router.post('/campaigns', response_model=AdminRaffleCampaignItem, status_code=status.HTTP_201_CREATED)
async def create_raffle_campaign(
    request: CreateRaffleCampaignRequest,
    admin: User = Depends(require_permission('raffle:create')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Create a draft raffle campaign."""
    prize_type = (request.prize_type or RafflePrizeType.CUSTOM.value).lower()
    if prize_type not in {e.value for e in RafflePrizeType}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid prize_type')

    if prize_type == RafflePrizeType.CUSTOM.value:
        if not (request.prize_text or '').strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='prize_text required for custom prize')
    elif request.prize_value is None or int(request.prize_value) <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='prize_value must be > 0')

    campaign = await raffle_crud.create_campaign(
        db,
        name=request.name.strip(),
        description=request.description,
        status=RaffleCampaignStatus.DRAFT.value,
        ends_at=request.ends_at,
        max_winners=request.max_winners,
        prize_type=prize_type,
        prize_value=request.prize_value,
        prize_text=request.prize_text,
    )
    logger.info('Admin created raffle campaign', campaign_id=campaign.id, admin_id=admin.id)
    return _campaign_item(campaign, {'tickets': 0, 'unique_users': 0, 'winners': 0})


@router.post('/campaigns/{campaign_id}/activate', response_model=AdminRaffleCampaignItem)
async def activate_raffle_campaign(
    campaign_id: int,
    admin: User = Depends(require_permission('raffle:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Activate a draft/closed campaign (requires RAFFLE_ENABLED)."""
    if not settings.is_raffle_enabled():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='RAFFLE_ENABLED is false')

    campaign = await raffle_crud.get_campaign_by_id(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Campaign not found')

    campaign = await raffle_crud.set_campaign_status(db, campaign, RaffleCampaignStatus.ACTIVE.value)
    stats = await raffle_crud.get_campaign_ticket_stats(db, campaign.id)
    logger.info('Admin activated raffle campaign', campaign_id=campaign.id, admin_id=admin.id)
    return _campaign_item(campaign, stats)


@router.post('/campaigns/{campaign_id}/close', response_model=AdminRaffleCampaignItem)
async def close_raffle_campaign(
    campaign_id: int,
    admin: User = Depends(require_permission('raffle:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Close an active campaign (stop issuing tickets)."""
    campaign = await raffle_crud.get_campaign_by_id(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Campaign not found')

    campaign = await raffle_crud.set_campaign_status(db, campaign, RaffleCampaignStatus.CLOSED.value)
    stats = await raffle_crud.get_campaign_ticket_stats(db, campaign.id)
    logger.info('Admin closed raffle campaign', campaign_id=campaign.id, admin_id=admin.id)
    return _campaign_item(campaign, stats)


@router.post('/campaigns/{campaign_id}/draw', response_model=AdminRaffleDrawResponse)
async def draw_raffle_campaign(
    campaign_id: int,
    admin: User = Depends(require_permission('raffle:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Run the weighted draw for a campaign."""
    campaign = await raffle_crud.get_campaign_by_id(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Campaign not found')

    try:
        winners = await draw_winners(db, campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    campaign = await raffle_crud.get_campaign_by_id(db, campaign_id)
    logger.info(
        'Admin drew raffle campaign',
        campaign_id=campaign_id,
        winners=len(winners),
        admin_id=admin.id,
    )
    return AdminRaffleDrawResponse(
        campaign_id=campaign_id,
        status=campaign.status if campaign else RaffleCampaignStatus.DRAWN.value,
        winners=[
            AdminRaffleWinnerItem(
                id=w.id,
                user_id=w.user_id,
                ticket_code=w.ticket_code,
                place=w.place,
                prize_type=w.prize_type,
                prize_value=w.prize_value,
                prize_text=w.prize_text,
                awarded=w.awarded,
            )
            for w in winners
        ],
    )
