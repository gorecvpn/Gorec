"""Сервис розыгрыша: выдача билетов за оплату подписки и жеребьёвка."""

from __future__ import annotations

import html
import random
import secrets
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud import raffle as raffle_crud
from app.database.crud.subscription import get_subscription_by_user_id
from app.database.crud.user import add_user_balance, get_user_by_id
from app.database.models import (
    RaffleCampaign,
    RaffleCampaignStatus,
    RafflePrizeType,
    RaffleTicket,
    RaffleWinner,
)


logger = structlog.get_logger(__name__)


def _make_ticket_code() -> str:
    return f'RAFFLE_{secrets.token_hex(4).upper()}'


async def issue_for_purchase(
    db: AsyncSession,
    user_id: int,
    transaction_id: int,
    tariff_id: int | None = None,
) -> RaffleTicket | None:
    """Выдать 1 билет за оплаченную подписку.

    Идемпотентно по (campaign_id, source_transaction_id).
    None — если RAFFLE_ENABLED=false / нет активной кампании.
    """
    if not settings.is_raffle_enabled():
        return None
    if not user_id or not transaction_id:
        return None

    campaign = await raffle_crud.get_current_active_campaign(db)
    if campaign is None:
        return None

    existing = await raffle_crud.get_ticket_by_campaign_tx(db, campaign.id, transaction_id)
    if existing is not None:
        return existing

    for _ in range(5):
        code = _make_ticket_code()
        try:
            async with db.begin_nested():
                ticket = await raffle_crud.create_ticket(
                    db,
                    campaign_id=campaign.id,
                    user_id=user_id,
                    ticket_code=code,
                    source_transaction_id=transaction_id,
                    tariff_id=tariff_id,
                    commit=False,
                )
            await db.commit()
            await db.refresh(ticket)
            logger.info(
                'Выдан билет розыгрыша',
                ticket_code=ticket.ticket_code,
                user_id=user_id,
                campaign_id=campaign.id,
                transaction_id=transaction_id,
            )
            await _notify_user_ticket(db, user_id, ticket, campaign)
            return ticket
        except IntegrityError:
            existing = await raffle_crud.get_ticket_by_campaign_tx(db, campaign.id, transaction_id)
            if existing is not None:
                return existing
            logger.debug('Коллизия кода билета, повтор', campaign_id=campaign.id)

    logger.warning('Не удалось выдать билет розыгрыша', user_id=user_id, transaction_id=transaction_id)
    return None


async def _notify_user_ticket(
    db: AsyncSession,
    user_id: int,
    ticket: RaffleTicket,
    campaign: RaffleCampaign,
) -> None:
    try:
        user = await get_user_by_id(db, user_id)
        if not user or not getattr(user, 'telegram_id', None):
            return

        text = (
            '🎟 Вам выдан билет розыгрыша!\n\n'
            f'Кампания: <b>{html.escape(campaign.name)}</b>\n'
            f'Код билета: <code>{html.escape(ticket.ticket_code)}</code>'
        )

        from app.bot_factory import create_bot
        from app.services.notification_delivery_service import notification_delivery_service
        from app.services.notification_types import NotificationType

        bot = create_bot()
        try:
            await notification_delivery_service.send_notification(
                user=user,
                notification_type=NotificationType.RAFFLE_TICKET,
                context={'ticket_code': ticket.ticket_code, 'campaign_name': campaign.name},
                bot=bot,
                telegram_message=text,
            )
        finally:
            await bot.session.close()
    except Exception as exc:
        logger.debug('Не удалось уведомить о билете розыгрыша', user_id=user_id, error=exc)


async def draw_winners(db: AsyncSession, campaign_id: int) -> list[RaffleWinner]:
    """Взвешенный выбор уникальных пользователей по числу билетов; статус → drawn."""
    campaign = await raffle_crud.get_campaign_by_id(db, campaign_id)
    if campaign is None:
        raise ValueError(f'Campaign {campaign_id} not found')

    if campaign.status == RaffleCampaignStatus.DRAWN.value:
        return await raffle_crud.list_winners(db, campaign_id)

    tickets = await raffle_crud.list_tickets_for_campaign(db, campaign_id)
    if not tickets:
        campaign.status = RaffleCampaignStatus.DRAWN.value
        campaign.updated_at = datetime.now(UTC)
        await db.commit()
        return []

    by_user: dict[int, list[RaffleTicket]] = defaultdict(list)
    for ticket in tickets:
        by_user[ticket.user_id].append(ticket)

    remaining = {uid: list(ts) for uid, ts in by_user.items()}
    winners: list[RaffleWinner] = []
    max_winners = max(1, int(campaign.max_winners or 1))

    for place in range(1, max_winners + 1):
        if not remaining:
            break
        population: list[int] = []
        for uid, ts in remaining.items():
            population.extend([uid] * len(ts))
        if not population:
            break
        chosen_user = random.choice(population)
        user_tickets = remaining.pop(chosen_user)
        winning_ticket = random.choice(user_tickets)

        awarded = False
        awarded_at = None
        try:
            awarded = await _try_award_prize(db, campaign, chosen_user)
            if awarded:
                awarded_at = datetime.now(UTC)
        except Exception as exc:
            logger.warning(
                'Автоначисление приза розыгрыша не удалось',
                campaign_id=campaign_id,
                user_id=chosen_user,
                error=exc,
            )

        winner = await raffle_crud.create_winner(
            db,
            campaign_id=campaign.id,
            user_id=chosen_user,
            ticket_id=winning_ticket.id,
            ticket_code=winning_ticket.ticket_code,
            place=place,
            prize_type=campaign.prize_type,
            prize_value=campaign.prize_value,
            prize_text=campaign.prize_text,
            awarded=awarded,
            awarded_at=awarded_at,
            commit=False,
        )
        winners.append(winner)

    campaign.status = RaffleCampaignStatus.DRAWN.value
    campaign.updated_at = datetime.now(UTC)
    await db.commit()

    for w in winners:
        await db.refresh(w)

    await _notify_admins_draw(db, campaign, winners)
    return winners


async def _try_award_prize(db: AsyncSession, campaign: RaffleCampaign, user_id: int) -> bool:
    prize_type = (campaign.prize_type or RafflePrizeType.CUSTOM.value).lower()
    prize_value = campaign.prize_value

    if prize_type == RafflePrizeType.CUSTOM.value or prize_value is None or int(prize_value) <= 0:
        return False

    user = await get_user_by_id(db, user_id)
    if user is None:
        return False

    if prize_type == RafflePrizeType.BALANCE.value:
        await add_user_balance(
            db,
            user,
            int(prize_value),
            description=f'Приз розыгрыша «{campaign.name}»',
            create_transaction=True,
            commit=False,
        )
        return True

    if prize_type == RafflePrizeType.DAYS.value:
        if settings.is_multi_tariff_enabled():
            return False
        from app.services.grace_access_echo import undo_grace_overlay_echo
        from app.services.subscription_service import SubscriptionService

        subscription = await get_subscription_by_user_id(db, user_id)
        if subscription is None:
            return False
        await undo_grace_overlay_echo(db, subscription)
        base = subscription.end_date or datetime.now(UTC)
        subscription.end_date = base + timedelta(days=int(prize_value))
        subscription.updated_at = datetime.now(UTC)
        try:
            await SubscriptionService().update_remnawave_user(db, subscription)
        except Exception as exc:
            logger.warning('RemnaWave sync после приза дней розыгрыша', user_id=user_id, error=exc)
        return True

    return False


async def _notify_admins_draw(
    db: AsyncSession,
    campaign: RaffleCampaign,
    winners: list[RaffleWinner],
) -> None:
    try:
        lines = [
            '🎲 <b>Розыгрыш завершён</b>',
            f'Кампания: <b>{html.escape(campaign.name)}</b> (#{campaign.id})',
            f'Приз: {html.escape(campaign.prize_type or "")}'
            + (f' / {campaign.prize_value}' if campaign.prize_value else '')
            + (f' — {html.escape(campaign.prize_text)}' if campaign.prize_text else ''),
            '',
            'Победители:',
        ]
        if not winners:
            lines.append('Билетов не было.')
        for w in winners:
            user = await get_user_by_id(db, w.user_id)
            uname = f'@{user.username}' if user and user.username else f'user#{w.user_id}'
            award_mark = '✅' if w.awarded else '⏳ ручная выдача'
            lines.append(
                f'{w.place}. {html.escape(uname)} — <code>{html.escape(w.ticket_code or "")}</code> {award_mark}'
            )

        from app.bot_factory import create_bot
        from app.services.admin_notification_service import AdminNotificationService, NotificationCategory

        bot = create_bot()
        try:
            service = AdminNotificationService(bot)
            await service.send_admin_notification(
                '\n'.join(lines),
                category=NotificationCategory.PROMO,
            )
        finally:
            await bot.session.close()
    except Exception as exc:
        logger.debug('Не удалось уведомить админов о розыгрыше', campaign_id=campaign.id, error=exc)


class RaffleService:
    async def issue_for_purchase(self, db, user_id, transaction_id, tariff_id=None):
        return await issue_for_purchase(db, user_id, transaction_id, tariff_id=tariff_id)

    async def draw_winners(self, db, campaign_id):
        return await draw_winners(db, campaign_id)


raffle_service = RaffleService()
