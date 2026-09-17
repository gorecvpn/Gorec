"""Raffle tickets routes for cabinet — active campaign summary and user's tickets."""

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud import raffle as raffle_crud
from app.database.models import User

from ..dependencies import get_cabinet_db, get_current_cabinet_user


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/raffle', tags=['Cabinet Raffle'])


class RaffleCampaignSummary(BaseModel):
    id: int
    name: str
    description: str | None = None
    prize_type: str
    prize_value: int | None = None
    prize_text: str | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    status: str
    max_winners: int


class RaffleTicketItem(BaseModel):
    id: int
    ticket_code: str
    created_at: datetime
    campaign_id: int


class RaffleSummaryResponse(BaseModel):
    """Active campaign + current user's tickets. Empty when disabled / no campaign."""

    enabled: bool
    campaign: RaffleCampaignSummary | None = None
    tickets: list[RaffleTicketItem]
    ticket_count: int


@router.get('', response_model=RaffleSummaryResponse)
async def get_raffle_summary(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get active raffle campaign summary and the current user's tickets."""
    enabled = settings.is_raffle_enabled()
    if not enabled:
        return RaffleSummaryResponse(enabled=False, campaign=None, tickets=[], ticket_count=0)

    campaign = await raffle_crud.get_current_active_campaign(db)
    if campaign is None:
        return RaffleSummaryResponse(enabled=True, campaign=None, tickets=[], ticket_count=0)

    tickets = await raffle_crud.list_tickets_for_user(db, user.id, campaign_id=campaign.id)
    return RaffleSummaryResponse(
        enabled=True,
        campaign=RaffleCampaignSummary(
            id=campaign.id,
            name=campaign.name,
            description=campaign.description,
            prize_type=campaign.prize_type,
            prize_value=campaign.prize_value,
            prize_text=campaign.prize_text,
            starts_at=campaign.starts_at,
            ends_at=campaign.ends_at,
            status=campaign.status,
            max_winners=campaign.max_winners,
        ),
        tickets=[
            RaffleTicketItem(
                id=t.id,
                ticket_code=t.ticket_code,
                created_at=t.created_at,
                campaign_id=t.campaign_id,
            )
            for t in tickets
        ],
        ticket_count=len(tickets),
    )
