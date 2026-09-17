"""Weighted raffle draw picks up to max_winners unique users."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.models import RaffleCampaignStatus
from app.services.raffle import service as raffle_service


def _ticket(tid: int, user_id: int, code: str) -> SimpleNamespace:
    return SimpleNamespace(id=tid, user_id=user_id, ticket_code=code)


@pytest.fixture
def campaign():
    return SimpleNamespace(
        id=3,
        name='Multi',
        status=RaffleCampaignStatus.CLOSED.value,
        max_winners=3,
        prize_type='custom',
        prize_value=None,
        prize_text='Gift',
        updated_at=None,
    )


async def test_draw_winners_unique_up_to_max(monkeypatch, campaign):
    tickets = [
        _ticket(1, 10, 'A1'),
        _ticket(2, 10, 'A2'),
        _ticket(3, 20, 'B1'),
        _ticket(4, 30, 'C1'),
        _ticket(5, 30, 'C2'),
        _ticket(6, 40, 'D1'),
    ]
    created: list[SimpleNamespace] = []

    async def _create_winner(db, **kwargs):
        w = SimpleNamespace(id=len(created) + 1, **kwargs)
        created.append(w)
        return w

    monkeypatch.setattr(raffle_service.raffle_crud, 'get_campaign_by_id', AsyncMock(return_value=campaign))
    monkeypatch.setattr(
        raffle_service.raffle_crud,
        'list_tickets_for_campaign',
        AsyncMock(return_value=tickets),
    )
    monkeypatch.setattr(raffle_service.raffle_crud, 'create_winner', AsyncMock(side_effect=_create_winner))
    monkeypatch.setattr(raffle_service, '_try_award_prize', AsyncMock(return_value=False))
    monkeypatch.setattr(raffle_service, '_notify_admins_draw', AsyncMock())

    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
    winners = await raffle_service.draw_winners(db, campaign.id)

    assert len(winners) == 3
    user_ids = [w.user_id for w in winners]
    assert len(set(user_ids)) == 3
    assert {w.place for w in winners} == {1, 2, 3}
    assert all(w.ticket_code for w in winners)
    assert campaign.status == RaffleCampaignStatus.DRAWN.value


async def test_draw_winners_caps_at_unique_users(monkeypatch, campaign):
    campaign.max_winners = 5
    tickets = [_ticket(1, 10, 'A1'), _ticket(2, 20, 'B1')]
    created: list[SimpleNamespace] = []

    async def _create_winner(db, **kwargs):
        w = SimpleNamespace(id=len(created) + 1, **kwargs)
        created.append(w)
        return w

    monkeypatch.setattr(raffle_service.raffle_crud, 'get_campaign_by_id', AsyncMock(return_value=campaign))
    monkeypatch.setattr(
        raffle_service.raffle_crud,
        'list_tickets_for_campaign',
        AsyncMock(return_value=tickets),
    )
    monkeypatch.setattr(raffle_service.raffle_crud, 'create_winner', AsyncMock(side_effect=_create_winner))
    monkeypatch.setattr(raffle_service, '_try_award_prize', AsyncMock(return_value=False))
    monkeypatch.setattr(raffle_service, '_notify_admins_draw', AsyncMock())

    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
    winners = await raffle_service.draw_winners(db, campaign.id)

    assert len(winners) == 2
    assert {w.user_id for w in winners} == {10, 20}


async def test_draw_already_drawn_returns_existing(monkeypatch):
    campaign = SimpleNamespace(id=9, status=RaffleCampaignStatus.DRAWN.value)
    existing = [SimpleNamespace(id=1, user_id=1, place=1)]
    monkeypatch.setattr(raffle_service.raffle_crud, 'get_campaign_by_id', AsyncMock(return_value=campaign))
    monkeypatch.setattr(raffle_service.raffle_crud, 'list_winners', AsyncMock(return_value=existing))

    result = await raffle_service.draw_winners(SimpleNamespace(), 9)
    assert result is existing
