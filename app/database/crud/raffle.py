"""CRUD для кампаний и билетов розыгрыша за покупку подписки."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    RaffleCampaign,
    RaffleCampaignStatus,
    RaffleTicket,
    RaffleWinner,
)


logger = structlog.get_logger(__name__)


async def create_campaign(
    db: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    status: str = RaffleCampaignStatus.DRAFT.value,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    max_winners: int = 1,
    prize_type: str = 'custom',
    prize_value: int | None = None,
    prize_text: str | None = None,
) -> RaffleCampaign:
    campaign = RaffleCampaign(
        name=name,
        description=description,
        status=status,
        starts_at=starts_at or datetime.now(UTC),
        ends_at=ends_at,
        max_winners=max(1, int(max_winners or 1)),
        prize_type=prize_type,
        prize_value=prize_value,
        prize_text=prize_text,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    logger.info('Создана кампания розыгрыша', campaign_id=campaign.id, name=campaign.name)
    return campaign


async def get_campaign_by_id(db: AsyncSession, campaign_id: int) -> RaffleCampaign | None:
    result = await db.execute(select(RaffleCampaign).where(RaffleCampaign.id == campaign_id))
    return result.scalar_one_or_none()


async def list_campaigns(db: AsyncSession, *, limit: int = 50, offset: int = 0) -> list[RaffleCampaign]:
    result = await db.execute(
        select(RaffleCampaign).order_by(RaffleCampaign.id.desc()).offset(offset).limit(limit)
    )
    return list(result.scalars().all())


async def set_campaign_status(db: AsyncSession, campaign: RaffleCampaign, status: str) -> RaffleCampaign:
    campaign.status = status
    campaign.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def get_current_active_campaign(db: AsyncSession) -> RaffleCampaign | None:
    """Активная кампания в окне дат. Если несколько — с самым поздним starts_at."""
    now = datetime.now(UTC)
    result = await db.execute(
        select(RaffleCampaign)
        .where(
            and_(
                RaffleCampaign.status == RaffleCampaignStatus.ACTIVE.value,
                RaffleCampaign.starts_at <= now,
                (RaffleCampaign.ends_at.is_(None)) | (RaffleCampaign.ends_at >= now),
            )
        )
        .order_by(RaffleCampaign.starts_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_ticket_by_campaign_tx(
    db: AsyncSession, campaign_id: int, source_transaction_id: int
) -> RaffleTicket | None:
    result = await db.execute(
        select(RaffleTicket).where(
            RaffleTicket.campaign_id == campaign_id,
            RaffleTicket.source_transaction_id == source_transaction_id,
        )
    )
    return result.scalar_one_or_none()


async def create_ticket(
    db: AsyncSession,
    *,
    campaign_id: int,
    user_id: int,
    ticket_code: str,
    source_transaction_id: int,
    tariff_id: int | None = None,
    commit: bool = True,
) -> RaffleTicket:
    ticket = RaffleTicket(
        campaign_id=campaign_id,
        user_id=user_id,
        ticket_code=ticket_code,
        source_transaction_id=source_transaction_id,
        tariff_id=tariff_id,
    )
    db.add(ticket)
    if commit:
        await db.commit()
    else:
        await db.flush()
    await db.refresh(ticket)
    return ticket


async def get_campaign_ticket_stats(db: AsyncSession, campaign_id: int) -> dict[str, int]:
    tickets_q = await db.execute(
        select(func.count(RaffleTicket.id)).where(RaffleTicket.campaign_id == campaign_id)
    )
    users_q = await db.execute(
        select(func.count(func.distinct(RaffleTicket.user_id))).where(
            RaffleTicket.campaign_id == campaign_id
        )
    )
    winners_q = await db.execute(
        select(func.count(RaffleWinner.id)).where(RaffleWinner.campaign_id == campaign_id)
    )
    return {
        'tickets': int(tickets_q.scalar() or 0),
        'unique_users': int(users_q.scalar() or 0),
        'winners': int(winners_q.scalar() or 0),
    }


async def list_tickets_for_campaign(db: AsyncSession, campaign_id: int) -> list[RaffleTicket]:
    result = await db.execute(select(RaffleTicket).where(RaffleTicket.campaign_id == campaign_id))
    return list(result.scalars().all())


async def list_winners(db: AsyncSession, campaign_id: int) -> list[RaffleWinner]:
    result = await db.execute(
        select(RaffleWinner)
        .options(selectinload(RaffleWinner.user))
        .where(RaffleWinner.campaign_id == campaign_id)
        .order_by(RaffleWinner.place.asc())
    )
    return list(result.scalars().all())


async def create_winner(
    db: AsyncSession,
    *,
    campaign_id: int,
    user_id: int,
    ticket_id: int | None,
    ticket_code: str | None,
    place: int,
    prize_type: str | None,
    prize_value: int | None,
    prize_text: str | None,
    awarded: bool = False,
    awarded_at: datetime | None = None,
    commit: bool = False,
) -> RaffleWinner:
    winner = RaffleWinner(
        campaign_id=campaign_id,
        user_id=user_id,
        ticket_id=ticket_id,
        ticket_code=ticket_code,
        place=place,
        prize_type=prize_type,
        prize_value=prize_value,
        prize_text=prize_text,
        awarded=awarded,
        awarded_at=awarded_at,
    )
    db.add(winner)
    if commit:
        await db.commit()
    else:
        await db.flush()
    await db.refresh(winner)
    return winner


async def list_tickets_for_user(
    db: AsyncSession,
    user_id: int,
    *,
    campaign_id: int | None = None,
    limit: int = 100,
) -> list[RaffleTicket]:
    """Билеты пользователя, опционально в рамках одной кампании (новые сверху)."""
    stmt = select(RaffleTicket).where(RaffleTicket.user_id == user_id)
    if campaign_id is not None:
        stmt = stmt.where(RaffleTicket.campaign_id == campaign_id)
    stmt = stmt.order_by(RaffleTicket.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
