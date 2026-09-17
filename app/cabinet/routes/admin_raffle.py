"""Admin raffle campaign management for cabinet."""

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud import raffle as raffle_crud
from app.database.models import RaffleCampaignStatus, RafflePrizeType, RaffleWinner, User
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
    updated_at: datetime | None = None
    drawn_at: datetime | None = None


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
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class AdminRaffleWinnerItem(BaseModel):
    id: int
    campaign_id: int
    user_id: int
    telegram_id: int | None = None
    username: str | None = None
    first_name: str | None = None
    display_name: str | None = None
    ticket_id: int | None = None
    ticket_code: str | None = None
    place: int
    prize_type: str | None = None
    prize_value: int | None = None
    prize_text: str | None = None
    awarded: bool
    awarded_at: datetime | None = None
    created_at: datetime | None = None


class AdminRaffleDrawResponse(BaseModel):
    campaign_id: int
    status: str
    drawn_at: datetime | None = None
    winners: list[AdminRaffleWinnerItem]


class AdminRaffleCampaignDetailResponse(BaseModel):
    enabled: bool
    campaign: AdminRaffleCampaignItem
    winners: list[AdminRaffleWinnerItem]


def _user_display(user: User | None, user_id: int) -> tuple[int | None, str | None, str | None, str]:
    if user is None:
        return None, None, None, f'user#{user_id}'
    telegram_id = getattr(user, 'telegram_id', None)
    username = getattr(user, 'username', None) or None
    first_name = getattr(user, 'first_name', None) or None
    if username:
        display = f'@{username}'
    elif first_name:
        display = first_name
    elif telegram_id:
        display = f'tg:{telegram_id}'
    else:
        display = f'user#{user_id}'
    return telegram_id, username, first_name, display


def _winner_item(winner: RaffleWinner) -> AdminRaffleWinnerItem:
    user = getattr(winner, 'user', None)
    telegram_id, username, first_name, display_name = _user_display(user, winner.user_id)
    ticket_code = winner.ticket_code
    if not ticket_code:
        ticket = getattr(winner, 'ticket', None)
        if ticket is not None:
            ticket_code = getattr(ticket, 'ticket_code', None)
    return AdminRaffleWinnerItem(
        id=winner.id,
        campaign_id=winner.campaign_id,
        user_id=winner.user_id,
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        display_name=display_name,
        ticket_id=winner.ticket_id,
        ticket_code=ticket_code,
        place=winner.place,
        prize_type=winner.prize_type,
        prize_value=winner.prize_value,
        prize_text=winner.prize_text,
        awarded=bool(winner.awarded),
        awarded_at=winner.awarded_at,
        created_at=winner.created_at,
    )


def _campaign_drawn_at(campaign, winners: list[RaffleWinner] | None = None) -> datetime | None:
    if campaign.status != RaffleCampaignStatus.DRAWN.value:
        return None
    if winners:
        created = [w.created_at for w in winners if getattr(w, 'created_at', None)]
        if created:
            return max(created)
    return getattr(campaign, 'updated_at', None)


def _campaign_item(
    campaign,
    stats: dict[str, int],
    *,
    drawn_at: datetime | None = None,
) -> AdminRaffleCampaignItem:
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
        updated_at=getattr(campaign, 'updated_at', None),
        drawn_at=drawn_at if drawn_at is not None else _campaign_drawn_at(campaign),
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


@router.get('/campaigns/{campaign_id}', response_model=AdminRaffleCampaignDetailResponse)
async def get_raffle_campaign(
    campaign_id: int,
    admin: User = Depends(require_permission('raffle:read')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Campaign detail with full draw history (winners)."""
    campaign = await raffle_crud.get_campaign_by_id(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Campaign not found')

    stats = await raffle_crud.get_campaign_ticket_stats(db, campaign.id)
    winners = await raffle_crud.list_winners(db, campaign.id)
    drawn_at = _campaign_drawn_at(campaign, winners)
    return AdminRaffleCampaignDetailResponse(
        enabled=settings.is_raffle_enabled(),
        campaign=_campaign_item(campaign, stats, drawn_at=drawn_at),
        winners=[_winner_item(w) for w in winners],
    )


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
        starts_at=request.starts_at,
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
    """Run the weighted draw for a campaign (up to max_winners unique users)."""
    campaign = await raffle_crud.get_campaign_by_id(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Campaign not found')

    try:
        await draw_winners(db, campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    campaign = await raffle_crud.get_campaign_by_id(db, campaign_id)
    winners = await raffle_crud.list_winners(db, campaign_id)
    drawn_at = _campaign_drawn_at(campaign, winners) if campaign else None
    logger.info(
        'Admin drew raffle campaign',
        campaign_id=campaign_id,
        winners=len(winners),
        admin_id=admin.id,
    )
    return AdminRaffleDrawResponse(
        campaign_id=campaign_id,
        status=campaign.status if campaign else RaffleCampaignStatus.DRAWN.value,
        drawn_at=drawn_at,
        winners=[_winner_item(w) for w in winners],
    )
